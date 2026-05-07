"""Status output helpers."""

from __future__ import annotations

from .constants import APP_NAME
from .console import green, yellow
from .core import AppError, CommandRunner, require_commands
from .docker import DockerInspector
from .network import NetworkDetector
from .routing import RouteManager
from .utils import parse_env_file

def show_status(
    runner: CommandRunner, detector: NetworkDetector, route_manager: RouteManager
) -> None:
    """Print host network summary and configured container routes."""

    require_commands(["ip", "systemctl"])
    print("Host")
    try:
        wan = detector.wan()
        print(f"  WAN: {wan.iface} {wan.src_ip} {wan.subnet}")
    except AppError as exc:
        print(f"  WAN: unknown ({exc})")
    links = runner.stdout(["ip", "-brief", "link", "show"], check=False)
    wg_links = [line for line in links.splitlines() if line.split()[0].startswith("wg")]
    print("  WireGuard/WARP links:")
    if wg_links:
        for line in wg_links:
            print(f"    {line}")
    else:
        print("    none")
    print()
    print("Configured routes")
    print_configured_routes(route_manager)
    print_orphaned_routes(route_manager)


def print_configured_routes(route_manager: RouteManager) -> None:
    """Print configured routes with per-instance service status."""

    routes = route_manager.configured_routes()
    if not routes:
        print("No configured container routes.")
        return

    for env_file, container in routes:
        data = parse_env_file(env_file)
        instance = env_file.stem
        service = f"{APP_NAME}@{instance}.service"
        status = service_status(route_manager.runner, service)
        colored_status = green(status) if status == "active" else yellow(status)
        print(f"{data.get('CONTAINER_NAME', container)}")
        print(f"  instance: {instance}")
        print(f"  service: {service} ({colored_status})")
        print(f"  source: {data.get('SRC', 'unknown')}")
        print(f"  warp: {data.get('WARP_IF', 'unknown')}")
        print(
            f"  mark/table/prio: {data.get('MARK', '?')} / {data.get('TABLE', '?')} / {data.get('PRIO', '?')}"
        )


def service_status(runner: CommandRunner, service: str) -> str:
    """Return systemd active state for a service."""

    result = runner.run(["systemctl", "is-active", service], check=False, capture=True)
    return (result.stdout or result.stderr).strip() or "unknown"


def print_orphaned_routes(route_manager: RouteManager) -> None:
    """Print configured routes whose Docker containers no longer exist."""

    docker = DockerInspector(route_manager.runner)
    orphaned: list[str] = []
    for env_file, container in route_manager.configured_routes():
        data = parse_env_file(env_file)
        name = data.get("CONTAINER_NAME") or container
        if not docker.exists(name):
            orphaned.append(name)
    if not orphaned:
        print()
        print(green("Orphaned routes: none"))
        return
    print()
    print(yellow("Orphaned routes"))
    for container in orphaned:
        print(yellow(f"  - {container}"))
    print(yellow("Run: sudo ./warp_container_routing.py route remove-orphaned"))
