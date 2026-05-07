"""Route validation checks."""

from __future__ import annotations

import shlex

from .constants import APP_NAME, CONFIG_DIR
from .console import green, yellow
from .core import AppError, CommandRunner, require_commands
from .docker import DockerInspector
from .network import NetworkDetector
from .utils import find_env_for_container, parse_env_file, state_file_for_env

class RouteChecker:
    """Validate that a configured route is present and can reach the internet."""

    def __init__(
        self, runner: CommandRunner, docker: DockerInspector, detector: NetworkDetector
    ) -> None:
        """Create a checker with host command, Docker and interface helpers."""

        self.runner = runner
        self.docker = docker
        self.detector = detector

    def check_all(self, external_url: str) -> int:
        """Run route checks for every configured container and aggregate the status."""

        require_commands(["docker", "ip", "iptables", "systemctl"])
        if not CONFIG_DIR.exists():
            print("No configured container routes.")
            return 0

        env_files = sorted(CONFIG_DIR.glob("*.env"))
        if not env_files:
            print("No configured container routes.")
            return 0

        exit_code = 0
        for index, env_file in enumerate(env_files):
            data = parse_env_file(env_file)
            container = data.get("CONTAINER_NAME")
            if index:
                print()
            if not container:
                print(f"env: {env_file}")
                print(yellow("result: failed"))
                print(yellow("  - CONTAINER_NAME is missing"))
                exit_code = 2
                continue
            print(f"== {container} ==")
            result = self.check(container, external_url)
            exit_code = max(exit_code, result)
        return exit_code

    def check(self, container: str, external_url: str) -> int:
        """Run all route checks and return a process-style status code."""

        require_commands(["docker", "ip", "iptables", "systemctl"])
        failures: list[str] = []
        warnings: list[str] = []

        try:
            info = self.docker.inspect(container)
            print(f"container: {container} ({info.container_id})")
            print(f"container IPv4: {info.ipv4}")
            if not info.running:
                failures.append("container is not running")
        except AppError as exc:
            print(f"container: failed ({exc})")
            return 2

        env_file = find_env_for_container(container)
        if not env_file:
            failures.append("routing env file not found")
            data = {}
        else:
            print(f"env: {env_file}")
            data = parse_env_file(env_file)
            self._check_service_status(env_file, failures)
            state_file = state_file_for_env(env_file)
            if state_file.exists():
                state = parse_env_file(state_file)
                data.update(state)
                print(f"state: {state_file}")

        if data:
            warp_if = data.get("WARP_IF", "")
            if not warp_if or not self.detector.have_iface(warp_if):
                failures.append(f"WARP interface is missing: {warp_if or 'unknown'}")
            else:
                print(green(f"warp interface: {warp_if} (present)"))
            self._check_ip_rule(data, failures)
            self._check_route_table(data, failures)
            self._check_iptables(data, failures)
            self._check_external_ip(container, external_url, warnings)

        if failures:
            print(yellow("result: failed"))
            for item in failures:
                print(yellow(f"  - {item}"))
            for item in warnings:
                print(yellow(f"  - warning: {item}"))
            return 2
        if warnings:
            print(yellow("result: warning"))
            for item in warnings:
                print(yellow(f"  - {item}"))
            return 1
        print(green("result: ok"))
        return 0

    def _check_ip_rule(self, data: dict[str, str], failures: list[str]) -> None:
        """Verify the fwmark policy rule points to the configured table."""

        out = self.runner.stdout(["ip", "rule", "show"], check=False)
        mark = data.get("MARK", "")
        table = data.get("TABLE", "")
        prio = data.get("PRIO", "")
        found = any(
            line.startswith(f"{prio}:")
            and f"fwmark {mark}" in line
            and f"lookup {table}" in line
            for line in out.splitlines()
        )
        if found:
            print(green("ip rule: present"))
        else:
            failures.append("ip rule for container mark is missing")

    def _check_route_table(self, data: dict[str, str], failures: list[str]) -> None:
        """Verify the routing table has a default route through WARP."""

        out = self.runner.stdout(
            ["ip", "route", "show", "table", data.get("TABLE", "")], check=False
        )
        if f"default dev {data.get('WARP_IF', '')}" in out:
            print(green("routing table: default route via WARP is present"))
        else:
            failures.append("routing table default route via WARP is missing")

    def _check_iptables(self, data: dict[str, str], failures: list[str]) -> None:
        """Verify mangle marking and NAT MASQUERADE rules are installed."""

        chain = data.get("CHAIN", "")
        src = data.get("SRC", "")
        warp_if = data.get("WARP_IF", "")
        mangle = self.runner.stdout(["iptables", "-t", "mangle", "-S"], check=False)
        nat = self.runner.stdout(["iptables", "-t", "nat", "-S"], check=False)
        if chain and chain in mangle and src in mangle:
            print(green("iptables mangle: present"))
        else:
            failures.append("iptables mangle chain/rules are missing")
        if src in nat and warp_if in nat and "MASQUERADE" in nat:
            print(green("iptables nat: MASQUERADE is present"))
        else:
            failures.append("iptables NAT MASQUERADE rule is missing")

    def _check_service_status(
        self, env_file, failures: list[str]
    ) -> None:
        """Verify the per-container routing systemd service is active."""

        service = f"{APP_NAME}@{env_file.stem}.service"
        result = self.runner.run(
            ["systemctl", "is-active", service], check=False, capture=True
        )
        status = (result.stdout or result.stderr).strip() or "unknown"
        line = f"service: {service} ({status})"
        print(green(line) if status == "active" else yellow(line))
        if status != "active":
            failures.append(f"systemd service is not active: {status}")

    def _check_external_ip(
        self, container: str, external_url: str, warnings: list[str]
    ) -> None:
        """Run the external HTTP check from inside the container when possible."""

        if self.docker.has_command(container, "curl"):
            cmd = f"curl -fsSL --max-time 15 {shlex.quote(external_url)}"
        elif self.docker.has_command(container, "wget"):
            cmd = f"wget -qO- --timeout=15 {shlex.quote(external_url)}"
        else:
            self._check_external_ip_with_nsenter(container, external_url, warnings)
            return
        result = self.runner.run(
            ["docker", "exec", container, "sh", "-c", cmd], check=False, capture=True
        )
        if result.returncode != 0:
            warnings.append("external IP check from container failed")
            return
        body = result.stdout.strip()
        print("external check:")
        for line in body.splitlines()[:8]:
            print(f"  {line}")

    def _check_external_ip_with_nsenter(
        self, container: str, external_url: str, warnings: list[str]
    ) -> None:
        """Fallback external HTTP check using the container network namespace."""

        if not self.runner.exists("nsenter") or not self.runner.exists("curl"):
            warnings.append(
                "container has no curl/wget and host has no nsenter+curl; skipped external IP check"
            )
            return
        try:
            info = self.docker.inspect(container)
        except AppError as exc:
            warnings.append(f"could not inspect container for nsenter check: {exc}")
            return
        if not info.pid:
            warnings.append(
                "container PID is unavailable; skipped nsenter external IP check"
            )
            return
        result = self.runner.run(
            [
                "nsenter",
                "-t",
                str(info.pid),
                "-n",
                "curl",
                "-fsSL",
                "--max-time",
                "15",
                external_url,
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            warnings.append("external IP check via nsenter failed")
            return
        body = result.stdout.strip()
        print("external check via nsenter:")
        for line in body.splitlines()[:8]:
            print(f"  {line}")
