"""Utility functions for env files, IDs and de-duplication."""

from __future__ import annotations

import hashlib
import ipaddress
import pathlib
import re
import shlex
from typing import Iterable

from .constants import CONFIG_DIR, DEFAULT_BASE_PRIORITY, DEFAULT_BASE_TABLE
from .core import AppError
from .models import RouteEntry


def safe_instance_name(container: str) -> str:
    """Build a systemd-safe instance name from a Docker container name or ID."""

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", container).strip(".-")
    slug = slug[:40] or "container"
    digest = hashlib.sha256(container.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def route_ids(
    container: str, base_table: int = DEFAULT_BASE_TABLE, salt: int = 0
) -> dict[str, str | int]:
    """Derive deterministic routing identifiers for a container and optional salt."""

    digest_input = container if salt == 0 else f"{container}\0{salt}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    mark_value = 0x100000 + (int(digest[:5], 16) & 0x0FFFFF)
    prio = DEFAULT_BASE_PRIORITY + (int(digest[5:9], 16) % 8000)
    table = base_table + (int(digest[9:12], 16) % 1000)
    chain = f"WCR_{digest[:16].upper()}"
    return {
        "instance": safe_instance_name(container),
        "mark": f"0x{mark_value:x}",
        "prio": prio,
        "table": table,
        "chain": chain,
    }


def allocate_route_ids(
    container: str, base_table: int = DEFAULT_BASE_TABLE
) -> dict[str, str | int]:
    """Allocate routing identifiers that do not collide with existing env files."""

    used_marks: set[str] = set()
    used_prios: set[str] = set()
    used_tables: set[str] = set()
    used_chains: set[str] = set()

    if CONFIG_DIR.exists():
        for path in sorted(CONFIG_DIR.glob("*.env")):
            data = parse_env_file(path)
            if data.get("CONTAINER_NAME") == container:
                continue
            used_marks.add(data.get("MARK", ""))
            used_prios.add(data.get("PRIO", ""))
            used_tables.add(data.get("TABLE", ""))
            used_chains.add(data.get("CHAIN", ""))

    for salt in range(10000):
        ids = route_ids(container, base_table, salt)
        if (
            str(ids["mark"]) not in used_marks
            and str(ids["prio"]) not in used_prios
            and str(ids["table"]) not in used_tables
            and str(ids["chain"]) not in used_chains
        ):
            return ids

    raise AppError(f"could not allocate unique routing IDs for container: {container}")


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Parse a shell-style KEY=value env file produced by this tool."""

    data: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        parts = shlex.split(value, posix=True)
        data[key] = parts[0] if parts else ""
    return data


def write_env_file(path: pathlib.Path, values: dict[str, str | int]) -> None:
    """Write a shell-safe env file with restrictive permissions."""

    lines = []
    for key, value in values.items():
        lines.append(f"{key}={shlex.quote(str(value))}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def dedupe_routes(routes: Iterable[RouteEntry]) -> list[RouteEntry]:
    """Return route entries without duplicate subnet/interface pairs."""

    seen: set[tuple[str, str]] = set()
    result: list[RouteEntry] = []
    for route in routes:
        key = (route.subnet, route.iface)
        if key in seen:
            continue
        seen.add(key)
        result.append(route)
    return result


def dedupe_strings(items: Iterable[str]) -> list[str]:
    """Return strings in original order with duplicates removed."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _is_ipv4_address(value: str) -> bool:
    """Return whether a string is a valid IPv4 address."""

    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def find_env_for_container(container: str) -> pathlib.Path | None:
    """Find the routing env file for a container name, ID or safe instance name."""

    direct = CONFIG_DIR / f"{safe_instance_name(container)}.env"
    if direct.exists():
        return direct
    if not CONFIG_DIR.exists():
        return None
    for path in sorted(CONFIG_DIR.glob("*.env")):
        try:
            data = parse_env_file(path)
        except OSError:
            continue
        if data.get("CONTAINER_NAME") == container:
            return path
    return None


def state_file_for_env(env_file: pathlib.Path) -> pathlib.Path:
    """Return the runtime state file path associated with an env file."""

    return env_file.with_name(env_file.name + ".state")
