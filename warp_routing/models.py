"""Small data models used across routing modules."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class ContainerInfo:
    """Docker container metadata required for routing and network namespace checks."""

    name: str
    container_id: str
    ipv4: str
    pid: int
    running: bool
    networks: list[dict[str, str]]


@dataclasses.dataclass(frozen=True)
class RouteEntry:
    """One route entry that can be written into the per-container env file."""

    subnet: str
    iface: str
    src_ip: str

    def as_env(self) -> str:
        """Serialize the route entry as subnet|iface|src_ip for the helper."""

        return f"{self.subnet}|{self.iface}|{self.src_ip}"
