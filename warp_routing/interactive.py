"""Simple stdin/stdout interactive menu for the CLI."""

from __future__ import annotations

from collections.abc import Callable

from .check import RouteChecker
from .constants import DEFAULT_BASE_TABLE, DEFAULT_EXTERNAL_URL, DEFAULT_PROFILE
from .core import AppError, CommandRunner
from .docker import DockerInspector
from .network import NetworkDetector
from .routing import RouteManager
from .status import show_status
from .warp import WarpManager


MenuAction = Callable[[], int | None]


def run_interactive(
    runner: CommandRunner,
    docker: DockerInspector,
    detector: NetworkDetector,
    route_manager: RouteManager,
    warp_manager: WarpManager,
    checker: RouteChecker,
) -> int:
    """Run a compact numbered menu when the script starts without arguments."""

    actions: dict[str, tuple[str, MenuAction]] = {
        "1": ("Status", lambda: _status(runner, detector, route_manager)),
        "2": ("Install WARP", lambda: _install_warp(warp_manager)),
        "3": (
            "Uninstall WARP",
            lambda: _uninstall_warp(route_manager, warp_manager),
        ),
        "4": ("Enable route for container", lambda: _enable_route(docker, route_manager)),
        "5": ("Reload routes", lambda: _reload_routes(route_manager)),
        "6": ("Check routes", lambda: _check_routes(route_manager, checker)),
        "7": ("Disable routes", lambda: _disable_routes(route_manager)),
        "8": ("Remove orphaned routes", route_manager.remove_orphaned),
        "0": ("Exit", lambda: 0),
    }

    while True:
        print()
        print("WARP Container Routing")
        print()
        for key, (title, _) in actions.items():
            print(f"{key}. {title}")
        choice = ask_text("Select", default="0")
        if choice == "0":
            return 0
        action = actions.get(choice)
        if not action:
            print("Unknown selection.")
            continue
        try:
            result = action[1]()
        except AppError as exc:
            print(f"Error: {exc}")
            result = 1
        if result:
            print(f"Command finished with status {result}.")


def ask_text(prompt: str, *, default: str | None = None) -> str:
    """Ask for a text value, optionally accepting a default on empty input."""

    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if value:
        return value
    if default is not None:
        return default
    return ""


def ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    """Ask a yes/no question."""

    marker = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{marker}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def choose_option(title: str, options: list[tuple[str, str]]) -> str | None:
    """Choose an item from a numbered list and return its value."""

    if not options:
        print("Nothing to choose.")
        return None
    print()
    print(title)
    for index, (label, _) in enumerate(options, start=1):
        print(f"{index}. {label}")
    print("0. Back")
    raw = ask_text("Select", default="0")
    try:
        choice = int(raw)
    except ValueError:
        print("Invalid selection.")
        return None
    if choice == 0:
        return None
    if not 1 <= choice <= len(options):
        print("Invalid selection.")
        return None
    return options[choice - 1][1]


def _status(
    runner: CommandRunner, detector: NetworkDetector, route_manager: RouteManager
) -> int:
    show_status(runner, detector, route_manager)
    return 0


def _install_warp(warp_manager: WarpManager) -> int:
    profile = ask_text("Profile", default=DEFAULT_PROFILE)
    warp_if = ask_optional("Expected WARP interface")
    warp_manager.install(profile, warp_if)
    return 0


def _uninstall_warp(route_manager: RouteManager, warp_manager: WarpManager) -> int:
    profile = ask_text("Profile", default=DEFAULT_PROFILE)
    if not confirm_disable_all_routes(
        route_manager,
        "The following container routes will be disabled before WARP uninstall:",
    ):
        return 1
    warp_manager.uninstall(profile)
    return 0


def _enable_route(docker: DockerInspector, route_manager: RouteManager) -> int:
    container = choose_running_container(docker, route_manager)
    if not container:
        return 0
    profile = ask_text("WARP profile", default=DEFAULT_PROFILE)
    warp_if = ask_optional("WARP interface")
    wan_if = ask_optional("WAN interface")
    base_table = ask_int("Base routing table", default=DEFAULT_BASE_TABLE)
    route_manager.enable(
        container,
        profile=profile,
        warp_if=warp_if,
        wan_if=wan_if,
        base_table=base_table,
    )
    return 0


def _reload_routes(route_manager: RouteManager) -> int:
    mode = choose_option(
        "Reload routes",
        [("All configured routes", "all"), ("One configured route", "one")],
    )
    if mode == "all":
        return route_manager.reload_all()
    if mode == "one":
        container = choose_configured_container(route_manager)
        if not container:
            return 0
        route_manager.reload(container)
        return 0
    return 0


def _check_routes(route_manager: RouteManager, checker: RouteChecker) -> int:
    mode = choose_option(
        "Check routes",
        [("All configured routes", "all"), ("One configured route", "one")],
    )
    if not mode:
        return 0
    external_url = ask_text("External check URL", default=DEFAULT_EXTERNAL_URL)
    if mode == "all":
        return checker.check_all(external_url)
    container = choose_configured_container(route_manager)
    if not container:
        return 0
    return checker.check(container, external_url)


def _disable_routes(route_manager: RouteManager) -> int:
    mode = choose_option(
        "Disable routes",
        [("All configured routes", "all"), ("One configured route", "one")],
    )
    if mode == "all":
        if confirm_disable_all_routes(
            route_manager, "The following container routes will be disabled:"
        ):
            route_manager.disable_all()
            return 0
        return 1
    if mode == "one":
        container = choose_configured_container(route_manager)
        if not container:
            return 0
        route_manager.disable(container)
        return 0
    return 0


def ask_optional(prompt: str) -> str | None:
    """Ask for an optional value."""

    value = ask_text(prompt, default="")
    return value or None


def ask_int(prompt: str, *, default: int) -> int:
    """Ask for an integer value."""

    value = ask_text(prompt, default=str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise AppError(f"invalid integer for {prompt}: {value}") from exc


def choose_running_container(
    docker: DockerInspector, route_manager: RouteManager
) -> str | None:
    """Choose a running Docker container that is not already configured."""

    configured = {container for _, container in route_manager.configured_routes()}
    containers = docker.running_containers()
    containers = [
        item
        for item in containers
        if item["name"] not in configured and item["id"] not in configured
    ]
    if not containers:
        print("No unconfigured running Docker containers found.")
        return None
    options = [
        (f"{item['name']} ({item['id']}, {item['image']})", item["name"])
        for item in containers
    ]
    return choose_option("Running Docker containers", options)


def choose_configured_container(route_manager: RouteManager) -> str | None:
    """Choose a container from configured route env files."""

    routes = route_manager.configured_routes()
    options = [(container, container) for _, container in routes]
    return choose_option("Configured container routes", options)


def confirm_disable_all_routes(route_manager: RouteManager, message: str) -> bool:
    """Print configured routes and ask whether they should all be disabled."""

    routes = route_manager.configured_routes()
    if not routes:
        print("No configured container routes.")
        return True
    print(message)
    for _, container in routes:
        print(f"  - {container}")
    return ask_yes_no("Continue?", default=False)
