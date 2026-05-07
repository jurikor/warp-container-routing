"""Docker inspection helpers."""

from __future__ import annotations

import json
import shlex

from .core import AppError, CommandRunner
from .models import ContainerInfo
from .utils import _is_ipv4_address


class DockerInspector:
    """Read Docker container metadata using the Docker CLI."""

    def __init__(self, runner: CommandRunner) -> None:
        """Create an inspector backed by the shared command runner."""

        self.runner = runner

    def inspect(self, container: str) -> ContainerInfo:
        """Return running state, PID and IPv4 metadata for a Docker container."""

        raw = self.runner.stdout(["docker", "inspect", container])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError(
                f"docker inspect returned invalid JSON for {container}"
            ) from exc
        if not payload:
            raise AppError(f"container not found: {container}")
        data = payload[0]
        networks = []
        for name, net in (
            data.get("NetworkSettings", {}).get("Networks") or {}
        ).items():
            ip = net.get("IPAddress") or ""
            if ip:
                networks.append(
                    {
                        "name": name,
                        "ip": ip,
                        "gateway": net.get("Gateway") or "",
                        "network_id": net.get("NetworkID") or "",
                    }
                )
        ipv4s = [net["ip"] for net in networks if _is_ipv4_address(net["ip"])]
        if not ipv4s:
            raise AppError(f"container has no IPv4 address: {container}")
        state = data.get("State") or {}
        return ContainerInfo(
            name=container,
            container_id=data.get("Id", "")[:12],
            ipv4=ipv4s[0],
            pid=int(state.get("Pid") or 0),
            running=bool(state.get("Running")),
            networks=networks,
        )

    def has_command(self, container: str, command: str) -> bool:
        """Return whether a command exists inside the container."""

        result = self.runner.run(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                f"command -v {shlex.quote(command)} >/dev/null 2>&1",
            ],
            check=False,
            capture=True,
        )
        return result.returncode == 0

    def exists(self, container: str) -> bool:
        """Return whether Docker can inspect the container by name or ID."""

        return (
            self.runner.run(
                ["docker", "inspect", container], check=False, capture=True
            ).returncode
            == 0
        )

    def running_containers(self) -> list[dict[str, str]]:
        """Return running Docker containers for interactive selection."""

        result = self.runner.run(
            ["docker", "ps", "--format", "{{json .}}"],
            check=False,
            capture=True,
        )
        containers: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(item.get("Names") or "")
            container_id = str(item.get("ID") or "")
            image = str(item.get("Image") or "")
            if not name:
                continue
            containers.append({"name": name, "id": container_id, "image": image})
        return containers
