"""Embedded helper script and systemd unit template."""

from __future__ import annotations


HELPER_SCRIPT = r'''#!/usr/bin/env python3
import ipaddress
import pathlib
import shlex
import subprocess
import sys
import time


def run(args, check=True, quiet=False):
    """Run a helper command, optionally suppressing expected cleanup noise."""

    completed = subprocess.run(
        [str(a) for a in args],
        check=False,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed.returncode


def stdout(args, check=True):
    """Run a helper command and return stdout for parsing."""

    completed = subprocess.run([str(a) for a in args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and completed.returncode != 0:
        print(completed.stderr.strip() or completed.stdout.strip(), file=sys.stderr)
        raise SystemExit(completed.returncode)
    return completed.stdout


def wait_for_iface(iface, timeout=30):
    """Wait until the WARP interface exists before applying routes."""

    for _ in range(timeout):
        if run(["ip", "link", "show", iface], check=False, quiet=True) == 0:
            return
        time.sleep(1)
    print(f"WARP interface is missing: {iface}", file=sys.stderr)
    raise SystemExit(1)


def load_env(path):
    """Load the systemd env file passed to the helper."""

    data = {}
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        parts = shlex.split(value, posix=True)
        data[key] = parts[0] if parts else ""
    return data


def write_env(path, data):
    """Write helper runtime state as shell-style key/value lines."""

    lines = []
    for key, value in data.items():
        lines.append(f"{key}={shlex.quote(str(value))}")
    pathlib.Path(path).write_text("\n".join(lines) + "\n")


def state_path(env_path):
    """Return the helper state file path for an env file."""

    return pathlib.Path(str(env_path) + ".state")


def load_state(env_path):
    """Load the last applied runtime state, if it exists."""

    path = state_path(env_path)
    if path.exists():
        return load_env(path)
    return {}


def save_state(env_path, data):
    """Persist the applied SRC and WARP interface for accurate cleanup."""

    write_env(state_path(env_path), data)


def remove_state(env_path):
    """Delete helper runtime state after route teardown."""

    state_path(env_path).unlink(missing_ok=True)


def get_container_ipv4(container):
    """Inspect Docker and return the container's current IPv4 address."""

    out = stdout(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container]).strip()
    for item in out.split():
        try:
            ipaddress.IPv4Address(item)
            return item
        except ValueError:
            continue
    print(f"container has no IPv4 address: {container}", file=sys.stderr)
    raise SystemExit(1)


def route_for_ip(ip):
    """Find the host bridge route that currently reaches a container IP."""

    target = ipaddress.ip_address(ip)
    out = stdout(["ip", "-4", "-o", "addr", "show", "scope", "global"], check=False)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1].split("@", 1)[0]
        if iface == "lo" or iface.startswith("veth"):
            continue
        interface = ipaddress.ip_interface(parts[3])
        if target in interface.network:
            return f"{interface.network}|{iface}|{interface.ip}"
    print(f"could not detect host bridge/interface for {ip}", file=sys.stderr)
    raise SystemExit(1)


def route_entries(env, container_ip):
    """Merge static env routes with the current container bridge route."""

    entries = []
    seen = set()
    for item in env.get("ROUTES", "").split(";"):
        if item and item not in seen:
            entries.append(item)
            seen.add(item)
    dynamic = route_for_ip(container_ip)
    if dynamic not in seen:
        entries.append(dynamic)
    return entries


def iptables_delete(args):
    """Delete all matching iptables rules without logging expected misses."""

    while run(["iptables", *args], check=False, quiet=True) == 0:
        pass


def iptables_ensure(args):
    """Append an iptables rule only when an equivalent rule is absent."""

    check_args = list(args)
    if "-A" in check_args:
        check_args[check_args.index("-A")] = "-C"
    elif "-I" in check_args:
        check_args[check_args.index("-I")] = "-C"
    else:
        return run(["iptables", *args])
    if run(["iptables", *check_args], check=False, quiet=True) != 0:
        run(["iptables", *args])


def up(env, env_path):
    """Apply routes, marks and NAT using the container's current Docker IP."""

    run(["modprobe", "br_netfilter"], check=False, quiet=True)
    run(["sysctl", "-w", "net.bridge.bridge-nf-call-iptables=1"], check=False)
    run(["sysctl", "-w", "net.bridge.bridge-nf-call-ip6tables=1"], check=False)

    table = env["TABLE"]
    mark = env["MARK"]
    prio = env["PRIO"]
    chain = env["CHAIN"]
    container = env["CONTAINER_NAME"]
    container_ip = get_container_ipv4(container)
    src = f"{container_ip}/32"
    warp_if = env["WARP_IF"]
    wait_for_iface(warp_if)

    run(["ip", "route", "flush", "table", table], check=False, quiet=True)
    for item in route_entries(env, container_ip):
        if not item:
            continue
        subnet, iface, ip = item.split("|", 2)
        run(["ip", "route", "replace", subnet, "dev", iface, "src", ip, "table", table])
    run(["ip", "route", "replace", "default", "dev", warp_if, "table", table])

    run(["ip", "rule", "del", "fwmark", mark, "lookup", table, "priority", prio], check=False, quiet=True)
    run(["ip", "rule", "add", "fwmark", mark, "lookup", table, "priority", prio])

    run(["iptables", "-t", "mangle", "-N", chain], check=False, quiet=True)
    run(["iptables", "-t", "mangle", "-F", chain])
    iptables_delete(["-t", "mangle", "-D", "PREROUTING", "-j", chain])
    iptables_delete(["-t", "mangle", "-D", "PREROUTING", "-m", "mark", "--mark", mark, "-j", "CONNMARK", "--save-mark"])

    iptables_ensure(["-t", "mangle", "-A", "PREROUTING", "-j", "CONNMARK", "--restore-mark"])
    iptables_ensure(["-t", "mangle", "-A", "PREROUTING", "-j", chain])
    iptables_ensure(["-t", "mangle", "-A", "PREROUTING", "-m", "mark", "--mark", mark, "-j", "CONNMARK", "--save-mark"])

    for dst in env.get("EXCLUDES", "").split():
        run(["iptables", "-t", "mangle", "-A", chain, "-s", src, "-d", dst, "-j", "RETURN"])
    run(["iptables", "-t", "mangle", "-A", chain, "-s", src, "-m", "conntrack", "--ctstate", "NEW", "-j", "MARK", "--set-mark", mark])

    iptables_delete(["-t", "nat", "-D", "POSTROUTING", "-s", src, "-o", warp_if, "-j", "MASQUERADE"])
    run(["iptables", "-t", "nat", "-A", "POSTROUTING", "-s", src, "-o", warp_if, "-j", "MASQUERADE"])
    save_state(env_path, {"SRC": src, "WARP_IF": warp_if})


def down(env, env_path):
    """Remove routes, marks and NAT using the last applied runtime state."""

    state = load_state(env_path)
    table = env["TABLE"]
    mark = env["MARK"]
    prio = env["PRIO"]
    chain = env["CHAIN"]
    src = state.get("SRC", env["SRC"])
    warp_if = state.get("WARP_IF", env["WARP_IF"])

    iptables_delete(["-t", "mangle", "-D", "PREROUTING", "-m", "mark", "--mark", mark, "-j", "CONNMARK", "--save-mark"])
    iptables_delete(["-t", "mangle", "-D", "PREROUTING", "-j", chain])
    run(["iptables", "-t", "mangle", "-F", chain], check=False, quiet=True)
    run(["iptables", "-t", "mangle", "-X", chain], check=False, quiet=True)
    iptables_delete(["-t", "nat", "-D", "POSTROUTING", "-s", src, "-o", warp_if, "-j", "MASQUERADE"])
    run(["ip", "rule", "del", "fwmark", mark, "lookup", table, "priority", prio], check=False, quiet=True)
    run(["ip", "route", "flush", "table", table], check=False, quiet=True)
    remove_state(env_path)


def main():
    """Dispatch helper actions for systemd ExecStart, ExecReload and ExecStop."""

    if len(sys.argv) != 3 or sys.argv[1] not in {"up", "down", "reload"}:
        print(f"usage: {sys.argv[0]} [up|down|reload] /path/to/env", file=sys.stderr)
        raise SystemExit(2)
    action = sys.argv[1]
    env_path = pathlib.Path(sys.argv[2])
    env = load_env(env_path)
    if action == "up":
        up(env, env_path)
    elif action == "down":
        down(env, env_path)
    else:
        down(env, env_path)
        up(env, env_path)


if __name__ == "__main__":
    main()
'''


SYSTEMD_UNIT_TEXT = """[Unit]
Description=Route Docker container %i egress through host WARP
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/warp-container-routing-helper up /etc/warp-container-routing/%i.env
ExecReload=/usr/local/sbin/warp-container-routing-helper reload /etc/warp-container-routing/%i.env
ExecStop=/usr/local/sbin/warp-container-routing-helper down /etc/warp-container-routing/%i.env

[Install]
WantedBy=multi-user.target
"""
