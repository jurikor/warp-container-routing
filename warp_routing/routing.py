"""Per-container route service management."""

from __future__ import annotations

import pathlib
import stat

from .constants import APP_NAME, CONFIG_DIR, HELPER_PATH, SYSTEMD_UNIT
from .core import AppError, CommandRunner, require_commands, require_root
from .docker import DockerInspector
from .helper_template import HELPER_SCRIPT, SYSTEMD_UNIT_TEXT
from .network import NetworkDetector
from .utils import (
    dedupe_routes,
    dedupe_strings,
    find_env_for_container,
    parse_env_file,
    route_ids,
    state_file_for_env,
    write_env_file,
)


class RouteManager:
    """Create, reload, disable and list per-container routing services."""

    def __init__(
        self, runner: CommandRunner, docker: DockerInspector, detector: NetworkDetector
    ) -> None:
        """Create a route manager with Docker and network helpers."""

        self.runner = runner
        self.docker = docker
        self.detector = detector

    def install_support_files(self) -> None:
        """Install the systemd template and helper used by all container routes."""

        if self.runner.dry_run:
            print(f"+ write {HELPER_PATH}")
            print(f"+ write {SYSTEMD_UNIT}")
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.chmod(0o700)
        HELPER_PATH.write_text(HELPER_SCRIPT)
        HELPER_PATH.chmod(
            HELPER_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        SYSTEMD_UNIT.write_text(SYSTEMD_UNIT_TEXT)
        self.runner.run(["systemctl", "daemon-reload"])

    def enable(
        self,
        container: str,
        *,
        profile: str,
        warp_if: str | None,
        wan_if: str | None,
        base_table: int,
    ) -> None:
        """Create an env file and start a routing service for a Docker container."""

        require_root()
        require_commands(["docker", "ip", "iptables", "systemctl"])
        if find_env_for_container(container):
            raise AppError(
                f"routing config already exists for container: {container}; use route reload or route disable first"
            )
        info = self.docker.inspect(container)
        if not info.running:
            raise AppError(f"container is not running: {container}")
        detected_warp_if = self.detector.detect_warp_iface(profile, warp_if)
        src = f"{info.ipv4}/32"
        self._ensure_src_has_no_host_rules(src, detected_warp_if)
        ids = self.allocate_route_ids(container, base_table)
        instance = str(ids["instance"])
        env_file = CONFIG_DIR / f"{instance}.env"
        values: dict[str, str | int] = {
            "CONTAINER_NAME": container,
            "TABLE": ids["table"],
            "MARK": ids["mark"],
            "PRIO": ids["prio"],
            "CHAIN": ids["chain"],
            "WARP_IF": detected_warp_if,
            **self.current_route_values(container, detected_warp_if, wan_if),
        }
        self.install_support_files()
        if self.runner.dry_run:
            print(f"+ write {env_file}")
        else:
            write_env_file(env_file, values)
        service = f"{APP_NAME}@{instance}.service"
        self.runner.run(["systemctl", "enable", service], check=False)
        self.runner.run(["systemctl", "restart", service])
        print(f"Routing enabled for {container}")
        print(f"  service: {service}")
        print(f"  env: {env_file}")
        print(f"  source: {info.ipv4}/32")
        print(f"  warp: {detected_warp_if}")
        if len(self.configured_routes()) > 1:
            print("Refreshing all configured route networks...")
            self.reload_all()

    def current_route_values(
        self, container: str, warp_if: str, wan_if: str | None
    ) -> dict[str, str]:
        """Build current source, local routes and excludes for a container route."""

        info = self.docker.inspect(container)
        if not info.running:
            raise AppError(f"container is not running: {container}")
        container_bridge = self.detector.interface_for_ip(info.ipv4)
        wan_route = self.detector.wan(wan_if)
        docker_routes = self.detector.docker_bridges(exclude={wan_route.iface, warp_if})
        routes = dedupe_routes([wan_route, *docker_routes, container_bridge])
        excludes = [
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            wan_route.subnet,
            "100.64.0.0/10",
        ]
        return {
            "CONTAINER_ID": info.container_id,
            "SRC": f"{info.ipv4}/32",
            "WAN_IF": wan_route.iface,
            "ROUTES": ";".join(route.as_env() for route in routes),
            "EXCLUDES": " ".join(dedupe_strings(excludes)),
        }

    def allocate_route_ids(
        self, container: str, base_table: int
    ) -> dict[str, str | int]:
        """Allocate route IDs that do not collide with tool configs or host state."""

        for salt in range(10000):
            ids = route_ids(container, base_table, salt)
            conflicts = self.route_id_conflicts(container, ids)
            if not conflicts:
                return ids
        raise AppError(
            f"could not allocate host-safe routing IDs for container: {container}"
        )

    def route_id_conflicts(
        self, container: str, ids: dict[str, str | int]
    ) -> list[str]:
        """Return conflicts for a candidate MARK/PRIO/TABLE/CHAIN set."""

        conflicts = self._route_id_env_conflicts(container, ids)
        table = str(ids["table"])
        prio = str(ids["prio"])
        mark = str(ids["mark"])
        chain = str(ids["chain"])

        if self._route_table_in_use(table):
            conflicts.append(f"routing table {table} is already in use")
        if self._ip_rule_priority_in_use(prio):
            conflicts.append(f"ip rule priority {prio} is already in use")
        if self._fwmark_in_use(mark):
            conflicts.append(f"fwmark {mark} is already in use")
        if self._iptables_chain_in_use(chain):
            conflicts.append(f"iptables chain {chain} is already in use")
        return conflicts

    def _route_id_env_conflicts(
        self, container: str, ids: dict[str, str | int]
    ) -> list[str]:
        """Return route ID conflicts with existing env files from this tool."""

        conflicts: list[str] = []
        if not CONFIG_DIR.exists():
            return conflicts
        for path in sorted(CONFIG_DIR.glob("*.env")):
            data = parse_env_file(path)
            if data.get("CONTAINER_NAME") == container:
                continue
            if data.get("MARK") == str(ids["mark"]):
                conflicts.append(f"mark {ids['mark']} is used by {path}")
            if data.get("PRIO") == str(ids["prio"]):
                conflicts.append(f"priority {ids['prio']} is used by {path}")
            if data.get("TABLE") == str(ids["table"]):
                conflicts.append(f"table {ids['table']} is used by {path}")
            if data.get("CHAIN") == str(ids["chain"]):
                conflicts.append(f"chain {ids['chain']} is used by {path}")
        return conflicts

    def _ensure_src_has_no_host_rules(self, src: str, warp_if: str) -> None:
        """Fail if host iptables rules already conflict with this route's source IP."""

        mangle = self.runner.stdout(["iptables", "-t", "mangle", "-S"], check=False)
        nat = self.runner.stdout(["iptables", "-t", "nat", "-S"], check=False)
        nat_conflict = any(
            src in line and f"-o {warp_if}" in line for line in nat.splitlines()
        )
        if src in mangle or nat_conflict:
            raise AppError(
                f"source {src} already appears in host iptables rules; "
                "run route reload/disable for stale rules or inspect iptables manually"
            )

    def _route_table_in_use(self, table: str) -> bool:
        """Return whether a routing table already has routes."""

        out = self.runner.stdout(["ip", "route", "show", "table", table], check=False)
        return bool(out.strip())

    def _ip_rule_priority_in_use(self, prio: str) -> bool:
        """Return whether an ip rule priority is already present."""

        out = self.runner.stdout(["ip", "rule", "show"], check=False)
        return any(line.startswith(f"{prio}:") for line in out.splitlines())

    def _fwmark_in_use(self, mark: str) -> bool:
        """Return whether a fwmark already appears in host rules."""

        lowered_mark = mark.lower()
        ip_rules = self.runner.stdout(["ip", "rule", "show"], check=False).lower()
        mangle = self.runner.stdout(["iptables", "-t", "mangle", "-S"], check=False)
        return lowered_mark in ip_rules or lowered_mark in mangle.lower()

    def _iptables_chain_in_use(self, chain: str) -> bool:
        """Return whether an iptables mangle chain name is already present."""

        mangle = self.runner.stdout(["iptables", "-t", "mangle", "-S"], check=False)
        return (
            f"-N {chain}" in mangle
            or f"-A {chain}" in mangle
            or f"-j {chain}" in mangle
        )

    def disable(self, container: str) -> None:
        """Disable routing for a configured container by name or ID."""

        require_root()
        require_commands(["ip", "iptables", "systemctl"])
        env_file = find_env_for_container(container)
        if not env_file:
            raise AppError(f"routing config not found for container: {container}")
        self.disable_env_file(env_file)
        print(f"Routing disabled for {container}")

    def configured_routes(self) -> list[tuple[pathlib.Path, str]]:
        """Return configured route env files with their container names."""

        if not CONFIG_DIR.exists():
            return []
        routes: list[tuple[pathlib.Path, str]] = []
        for env_file in sorted(CONFIG_DIR.glob("*.env")):
            data = parse_env_file(env_file)
            routes.append((env_file, data.get("CONTAINER_NAME") or env_file.stem))
        return routes

    def disable_all(self) -> int:
        """Disable routing for every configured container."""

        require_root()
        require_commands(["ip", "iptables", "systemctl"])
        routes = self.configured_routes()
        if not routes:
            print("No configured container routes.")
            return 0

        disabled = 0
        for env_file, container in routes:
            self.disable_env_file(env_file)
            print(f"Routing disabled for {container}")
            disabled += 1
        return disabled

    def reload(self, container: str) -> None:
        """Reload routing rules using the container's current Docker IP address."""

        require_root()
        require_commands(["ip", "iptables", "systemctl"])
        env_file = find_env_for_container(container)
        if not env_file:
            raise AppError(f"routing config not found for container: {container}")
        self.reload_env_file(env_file, container)

    def reload_all(self) -> int:
        """Reload routing rules for every configured container route."""

        require_root()
        require_commands(["ip", "iptables", "systemctl"])
        if not CONFIG_DIR.exists():
            print("No configured container routes.")
            return 0

        env_files = sorted(CONFIG_DIR.glob("*.env"))
        if not env_files:
            print("No configured container routes.")
            return 0

        exit_code = 0
        for env_file in env_files:
            data = parse_env_file(env_file)
            container = data.get("CONTAINER_NAME")
            if not container:
                print(f"Routing reload failed for {env_file}: CONTAINER_NAME is missing")
                exit_code = 1
                continue
            try:
                self.reload_env_file(env_file, container)
            except AppError as exc:
                print(f"Routing reload failed for {container}: {exc}")
                exit_code = 1
        return exit_code

    def reload_env_file(self, env_file: pathlib.Path, container: str) -> None:
        """Re-apply one routing service identified by its env file."""

        self.rebuild_route_env_file(env_file, container)
        service = f"{APP_NAME}@{env_file.stem}.service"
        self.runner.run(["systemctl", "enable", service], check=False)
        self.runner.run(["systemctl", "restart", service])
        print(f"Routing reloaded for {container}")

    def rebuild_route_env_file(self, env_file: pathlib.Path, container: str) -> None:
        """Refresh dynamic env values before reloading a route service."""

        data = parse_env_file(env_file)
        warp_if = data.get("WARP_IF")
        if not warp_if:
            raise AppError(f"WARP_IF is missing in {env_file}")
        data.update(
            self.current_route_values(container, warp_if, data.get("WAN_IF") or None)
        )
        if self.runner.dry_run:
            print(f"+ refresh ROUTES/EXCLUDES in {env_file}")
            return
        write_env_file(env_file, data)

    def disable_env_file(self, env_file: pathlib.Path) -> None:
        """Stop and remove one routing service identified by its env file."""

        instance = env_file.stem
        service = f"{APP_NAME}@{instance}.service"
        self.runner.run(["systemctl", "stop", service], check=False)
        if HELPER_PATH.exists():
            self.runner.run([str(HELPER_PATH), "down", str(env_file)], check=False)
        self.runner.run(["systemctl", "disable", service], check=False)
        state_file_for_env(env_file).unlink(missing_ok=True)
        env_file.unlink(missing_ok=True)
        self.cleanup_support_files_if_unused()

    def remove_orphaned(self) -> int:
        """Remove routing configs whose Docker containers no longer exist."""

        require_root()
        require_commands(["docker", "ip", "iptables", "systemctl"])
        if not CONFIG_DIR.exists():
            print("No configured container routes.")
            return 0
        removed = 0
        for env_file in sorted(CONFIG_DIR.glob("*.env")):
            data = parse_env_file(env_file)
            container = data.get("CONTAINER_NAME")
            if not container:
                continue
            if self.docker.exists(container):
                continue
            self.disable_env_file(env_file)
            print(f"Removed orphaned routing config: {container}")
            removed += 1
        if removed == 0:
            print("No orphaned routing configs found.")
        return removed

    def cleanup_support_files_if_unused(self) -> None:
        """Remove shared helper/unit files when no route env files remain."""

        if CONFIG_DIR.exists() and any(CONFIG_DIR.glob("*.env")):
            return
        HELPER_PATH.unlink(missing_ok=True)
        SYSTEMD_UNIT.unlink(missing_ok=True)
        self.runner.run(["systemctl", "daemon-reload"], check=False)

    def list_routes(self) -> None:
        """Print all configured per-container routing env files."""

        if not CONFIG_DIR.exists():
            print("No configured container routes.")
            return
        files = sorted(CONFIG_DIR.glob("*.env"))
        if not files:
            print("No configured container routes.")
            return
        for path in files:
            data = parse_env_file(path)
            print(f"{data.get('CONTAINER_NAME', path.stem)}")
            print(f"  instance: {path.stem}")
            print(f"  source: {data.get('SRC', 'unknown')}")
            print(f"  warp: {data.get('WARP_IF', 'unknown')}")
            print(
                f"  mark/table/prio: {data.get('MARK', '?')} / {data.get('TABLE', '?')} / {data.get('PRIO', '?')}"
            )
