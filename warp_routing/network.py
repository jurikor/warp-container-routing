"""Host network detection helpers."""

from __future__ import annotations

import ipaddress
from typing import Iterable

from .constants import DEFAULT_PROFILE
from .core import AppError, CommandRunner
from .models import RouteEntry


class NetworkDetector:
    """Detect host interfaces, subnets and bridge routes needed for policy routing."""

    def __init__(self, runner: CommandRunner) -> None:
        """Create a detector backed by the shared command runner."""

        self.runner = runner

    def have_iface(self, iface: str) -> bool:
        """Return whether a network interface exists on the host."""

        return (
            self.runner.run(
                ["ip", "link", "show", iface], check=False, capture=True
            ).returncode
            == 0
        )

    def default_wan_iface(self) -> str:
        """Return the interface used by the host default IPv4 route."""

        out = self.runner.stdout(
            ["ip", "route", "show", "default", "0.0.0.0/0"], check=False
        )
        for line in out.splitlines():
            parts = line.split()
            if "dev" in parts:
                return parts[parts.index("dev") + 1]
        raise AppError("could not determine WAN interface from default route")

    def first_ipv4_on_iface(self, iface: str) -> tuple[str, str]:
        """Return the first IPv4 address and network found on an interface."""

        out = self.runner.stdout(
            ["ip", "-4", "-o", "addr", "show", "dev", iface], check=False
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                cidr = parts[3]
                ip = str(ipaddress.ip_interface(cidr).ip)
                return ip, str(ipaddress.ip_interface(cidr).network)
        raise AppError(f"could not determine IPv4 on interface {iface}")

    def wan(self, override_iface: str | None = None) -> RouteEntry:
        """Return a route entry for the host WAN network."""

        iface = override_iface or self.default_wan_iface()
        if not self.have_iface(iface):
            raise AppError(f"WAN interface not found: {iface}")
        ip, network = self.first_ipv4_on_iface(iface)
        return RouteEntry(network, iface, ip)

    def detect_warp_iface(
        self, profile: str = DEFAULT_PROFILE, override_iface: str | None = None
    ) -> str:
        """Resolve the WARP interface by explicit name, profile name or first wg* link."""

        if override_iface:
            if not self.have_iface(override_iface):
                raise AppError(f"WARP interface not found: {override_iface}")
            return override_iface
        if self.have_iface(profile):
            return profile
        out = self.runner.stdout(["ip", "-o", "link", "show"], check=False)
        for line in out.splitlines():
            parts = line.split(": ", 2)
            if len(parts) < 2:
                continue
            iface = parts[1].split("@", 1)[0]
            if iface.startswith("wg"):
                return iface
        raise AppError(
            "WARP/WireGuard interface not found; run 'warp install' or pass --warp-if"
        )

    def interface_for_ip(self, ip: str) -> RouteEntry:
        """Find the host interface whose subnet contains the given container IP."""

        target = ipaddress.ip_address(ip)
        out = self.runner.stdout(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"], check=False
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1].split("@", 1)[0]
            if iface == "lo" or iface.startswith("veth"):
                continue
            cidr = parts[3]
            interface = ipaddress.ip_interface(cidr)
            if target in interface.network:
                return RouteEntry(str(interface.network), iface, str(interface.ip))
        raise AppError(f"could not detect host bridge/interface for {ip}")

    def docker_bridges(self, *, exclude: Iterable[str] = ()) -> list[RouteEntry]:
        """Return route entries for Docker bridge interfaces except excluded links."""

        excluded = set(exclude)
        entries: list[RouteEntry] = []
        out = self.runner.stdout(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"], check=False
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1].split("@", 1)[0]
            if iface in excluded or iface == "lo" or iface.startswith("veth"):
                continue
            if not (iface == "docker0" or iface.startswith("br-")):
                continue
            interface = ipaddress.ip_interface(parts[3])
            entries.append(RouteEntry(str(interface.network), iface, str(interface.ip)))
        return entries
