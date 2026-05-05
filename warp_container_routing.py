#!/usr/bin/env python3
"""CLI entry point for WARP container routing."""

from __future__ import annotations

import argparse
import sys

from warp_routing import (
    DEFAULT_BASE_TABLE,
    DEFAULT_EXTERNAL_URL,
    DEFAULT_PROFILE,
    AppError,
    CommandRunner,
    DockerInspector,
    NetworkDetector,
    RouteChecker,
    RouteManager,
    WarpManager,
    run_interactive,
    show_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route Docker container egress through host-level WARP"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print commands without executing them"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print executed commands"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    warp = subparsers.add_parser("warp", help="manage host-level WARP")
    warp_sub = warp.add_subparsers(dest="warp_command", required=True)
    warp_install = warp_sub.add_parser("install", help="install and start wgcf WARP")
    warp_install.add_argument("--profile", default=DEFAULT_PROFILE)
    warp_install.add_argument("--warp-if", default=None)
    warp_uninstall = warp_sub.add_parser(
        "uninstall", help="stop and remove wgcf WARP profile"
    )
    warp_uninstall.add_argument("--profile", default=DEFAULT_PROFILE)
    warp_uninstall.add_argument(
        "--force",
        action="store_true",
        help="disable all configured container routes without confirmation",
    )

    route = subparsers.add_parser("route", help="manage container routing")
    route_sub = route.add_subparsers(dest="route_command", required=True)
    route_enable = route_sub.add_parser(
        "enable", help="route container egress through WARP"
    )
    route_enable.add_argument("--container", required=True)
    route_enable.add_argument("--profile", default=DEFAULT_PROFILE)
    route_enable.add_argument("--warp-if", default=None)
    route_enable.add_argument("--wan-if", default=None)
    route_enable.add_argument("--base-table", type=int, default=DEFAULT_BASE_TABLE)

    route_disable = route_sub.add_parser(
        "disable", help="remove routing for one container or all configured routes"
    )
    route_disable.add_argument("--container", default=None)
    route_disable.add_argument(
        "--force",
        action="store_true",
        help="disable all configured container routes without confirmation",
    )

    route_reload = route_sub.add_parser(
        "reload", help="refresh routing for one container or all configured routes"
    )
    route_reload.add_argument("--container", default=None)

    route_check = route_sub.add_parser(
        "check", help="check routing for one container or all configured routes"
    )
    route_check.add_argument("--container", default=None)
    route_check.add_argument("--external-url", default=DEFAULT_EXTERNAL_URL)

    route_sub.add_parser("list", help="list configured container routes")
    route_sub.add_parser(
        "remove-orphaned", help="remove configs for missing containers"
    )

    status = subparsers.add_parser("status", help="show host and route status")
    status.set_defaults(status=True)
    return parser


def confirm_disable_all_routes(
    route_manager: RouteManager, *, force: bool, dry_run: bool, message: str
) -> bool:
    """Ask for confirmation before removing all configured container routes."""

    routes = route_manager.configured_routes()
    if not routes:
        print("No configured container routes.")
        return False

    print(message)
    for _, container in routes:
        print(f"  - {container}")
    if dry_run:
        print("+ dry-run: confirmation skipped")
        return True
    if not force:
        try:
            answer = input("Continue? [y/N]: ").strip().lower()
        except EOFError as exc:
            raise AppError("aborted: confirmation is required") from exc
        if answer not in {"y", "yes"}:
            raise AppError("aborted")
    route_manager.disable_all()
    return True


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    runner = CommandRunner()
    docker = DockerInspector(runner)
    detector = NetworkDetector(runner)
    route_manager = RouteManager(runner, docker, detector)
    warp_manager = WarpManager(runner, detector)
    checker = RouteChecker(runner, docker, detector)

    if not argv:
        return run_interactive(
            runner, docker, detector, route_manager, warp_manager, checker
        )

    parser = build_parser()
    args = parser.parse_args(argv)
    runner = CommandRunner(dry_run=args.dry_run, verbose=args.verbose)
    docker = DockerInspector(runner)
    detector = NetworkDetector(runner)
    route_manager = RouteManager(runner, docker, detector)
    warp_manager = WarpManager(runner, detector)
    checker = RouteChecker(runner, docker, detector)

    if args.command == "warp" and args.warp_command == "install":
        warp_manager.install(args.profile, args.warp_if)
        return 0
    if args.command == "warp" and args.warp_command == "uninstall":
        confirm_disable_all_routes(
            route_manager,
            force=args.force,
            dry_run=args.dry_run,
            message="The following container routes will be disabled before WARP uninstall:",
        )
        warp_manager.uninstall(args.profile)
        return 0
    if args.command == "route":
        if args.route_command == "enable":
            route_manager.enable(
                args.container,
                profile=args.profile,
                warp_if=args.warp_if,
                wan_if=args.wan_if,
                base_table=args.base_table,
            )
            return 0
        if args.route_command == "disable":
            if args.container:
                route_manager.disable(args.container)
                return 0
            confirm_disable_all_routes(
                route_manager,
                force=args.force,
                dry_run=args.dry_run,
                message="The following container routes will be disabled:",
            )
            return 0
        if args.route_command == "reload":
            if args.container:
                route_manager.reload(args.container)
                return 0
            return route_manager.reload_all()
        if args.route_command == "check":
            if args.container:
                return checker.check(args.container, args.external_url)
            return checker.check_all(args.external_url)
        if args.route_command == "list":
            route_manager.list_routes()
            return 0
        if args.route_command == "remove-orphaned":
            route_manager.remove_orphaned()
            return 0
    if args.command == "status":
        show_status(runner, detector, route_manager)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
