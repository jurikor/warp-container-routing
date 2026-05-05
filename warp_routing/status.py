"""Status output helpers."""

from __future__ import annotations

from .core import AppError, CommandRunner, require_commands
from .docker import DockerInspector
from .network import NetworkDetector
from .routing import RouteManager
from .utils import parse_env_file


def show_status(
    runner: CommandRunner, detector: NetworkDetector, route_manager: RouteManager
) -> None:
    """Print host network summary and configured container routes."""

    require_commands(["ip"])
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
    route_manager.list_routes()
    print_orphaned_routes(route_manager)


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
        print("Orphaned routes: none")
        return
    print()
    print("Orphaned routes")
    for container in orphaned:
        print(f"  - {container}")
    print("Run: sudo ./warp_container_routing.py route remove-orphaned")
