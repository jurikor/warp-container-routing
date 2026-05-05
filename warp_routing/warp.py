"""Cloudflare WARP profile management."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import urllib.request

from .constants import DEFAULT_WARP_ENDPOINT
from .core import AppError, CommandRunner, require_commands, require_root
from .network import NetworkDetector


class WarpManager:
    """Install, patch, start and remove a host-level wgcf WARP profile."""

    def __init__(self, runner: CommandRunner, detector: NetworkDetector) -> None:
        """Create a WARP manager with command execution and interface detection."""

        self.runner = runner
        self.detector = detector

    def install(self, profile: str, warp_if: str | None = None) -> None:
        """Install wgcf if needed, ensure a Table=off profile, and start wg-quick."""

        require_root()
        require_commands(["ip", "systemctl", "curl"])
        self._install_packages()
        self._install_wgcf_binary()
        self._ensure_profile(profile)
        self.runner.run(["systemctl", "daemon-reload"])
        self.runner.run(
            ["systemctl", "enable", f"wg-quick@{profile}.service"], check=False
        )
        self.runner.run(["systemctl", "restart", f"wg-quick@{profile}.service"])
        expected = warp_if or profile
        for _ in range(10):
            if self.detector.have_iface(expected):
                print(f"WARP interface is up: {expected}")
                return
            time.sleep(1)
        raise AppError(f"WARP interface did not come up: {expected}")

    def uninstall(self, profile: str) -> None:
        """Stop and remove a wgcf profile, account file and installed wgcf binary."""

        require_root()
        require_commands(["ip", "systemctl"])
        service = f"wg-quick@{profile}.service"
        conf = pathlib.Path("/etc/wireguard") / f"{profile}.conf"
        account = pathlib.Path("/etc/wireguard/wgcf-account.toml")
        wgcf = pathlib.Path("/usr/local/bin/wgcf")

        self.runner.run(["systemctl", "stop", service], check=False)
        self.runner.run(["systemctl", "disable", service], check=False)
        if self.detector.have_iface(profile):
            self.runner.run(["ip", "link", "delete", profile], check=False)
        if self.runner.dry_run:
            print(f"+ remove {conf}")
            print(f"+ remove {account}")
            print(f"+ remove {wgcf}")
        else:
            conf.unlink(missing_ok=True)
            account.unlink(missing_ok=True)
            wgcf.unlink(missing_ok=True)
        self.runner.run(["systemctl", "daemon-reload"], check=False)
        print(f"WARP profile removed: {profile}")

    def _install_packages(self) -> None:
        """Install OS packages required for WireGuard and wgcf bootstrap."""

        packages: list[str] = []
        if self.runner.exists("apt-get"):
            mapping = {
                "curl": "curl",
                "wget": "wget",
                "tar": "tar",
                "ip": "iproute2",
                "iptables": "iptables",
                "wg": "wireguard-tools",
            }
            self.runner.run(["apt-get", "update", "-y"])
            for binary, package in mapping.items():
                if not self.runner.exists(binary):
                    packages.append(package)
            if packages:
                env = os.environ.copy()
                env["DEBIAN_FRONTEND"] = "noninteractive"
                if self.runner.verbose or self.runner.dry_run:
                    print(
                        "+ DEBIAN_FRONTEND=noninteractive apt-get install -y "
                        + " ".join(packages)
                    )
                if not self.runner.dry_run:
                    subprocess.run(
                        ["apt-get", "install", "-y", *packages], check=True, env=env
                    )
            return
        if self.runner.exists("dnf") or self.runner.exists("yum"):
            manager = "dnf" if self.runner.exists("dnf") else "yum"
            mapping = {
                "curl": "curl",
                "wget": "wget",
                "tar": "tar",
                "ip": "iproute",
                "iptables": "iptables",
                "wg": "wireguard-tools",
            }
            for binary, package in mapping.items():
                if not self.runner.exists(binary):
                    packages.append(package)
            if packages:
                self.runner.run([manager, "install", "-y", *packages])
            return
        raise AppError("unsupported package manager for automatic WARP install")

    def _install_wgcf_binary(self) -> None:
        """Download the latest wgcf binary for the host architecture if absent."""

        if self.runner.exists("wgcf"):
            return
        if self.runner.dry_run:
            print("+ install wgcf to /usr/local/bin/wgcf")
            return
        arch_map = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
            "armv7l": "armv7",
            "armv7": "armv7",
        }
        machine = os.uname().machine
        arch = arch_map.get(machine)
        if not arch:
            raise AppError(f"unsupported architecture for wgcf: {machine}")
        with urllib.request.urlopen(
            "https://api.github.com/repos/ViRb3/wgcf/releases/latest", timeout=30
        ) as response:
            release = json.loads(response.read().decode("utf-8"))
        url = ""
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            candidate = asset.get("browser_download_url", "")
            if name.endswith(f"linux_{arch}") or f"linux_{arch}" in candidate:
                url = candidate
                break
        if not url:
            raise AppError(f"could not find wgcf release for {arch}")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "wgcf"
            urllib.request.urlretrieve(url, path)
            path.chmod(0o755)
            shutil.copy2(path, "/usr/local/bin/wgcf")
            pathlib.Path("/usr/local/bin/wgcf").chmod(0o755)

    def _ensure_profile(self, profile: str) -> None:
        """Create or reuse a wgcf account and profile, then patch the profile."""

        wgdir = pathlib.Path("/etc/wireguard")
        conf = wgdir / f"{profile}.conf"
        account = wgdir / "wgcf-account.toml"
        legacy_account = pathlib.Path.home() / "wgcf-account.toml"
        if self.runner.dry_run:
            print(f"+ ensure WARP profile {conf} with Table = off")
            return
        wgdir.mkdir(parents=True, exist_ok=True)
        wgdir.chmod(0o700)
        if not account.exists() and legacy_account.exists():
            shutil.move(str(legacy_account), account)
        if not account.exists():
            self.runner.run(["wgcf", "register"], input_text="yes\n", cwd=wgdir)
        if not conf.exists():
            generated = wgdir / "wgcf-profile.conf"
            if generated.exists():
                generated.unlink()
            self.runner.run(["wgcf", "generate"], cwd=wgdir)
            if not generated.exists():
                raise AppError("wgcf generate did not create wgcf-profile.conf")
            generated.replace(conf)
        self._patch_profile(conf)

    @staticmethod
    def _patch_profile(conf: pathlib.Path) -> None:
        """Make a wgcf profile safe for host routing by disabling automatic routes."""

        text = conf.read_text()
        lines = [line for line in text.splitlines() if not line.startswith("DNS = ")]
        lines = [
            f"Endpoint = {DEFAULT_WARP_ENDPOINT}"
            if line.startswith("Endpoint = ")
            else line
            for line in lines
        ]
        text = "\n".join(lines) + "\n"
        if "Table = off" not in text:
            marker = "[Interface]\n"
            if marker not in text:
                raise AppError(f"missing [Interface] section in {conf}")
            text = text.replace(marker, marker + "Table = off\n", 1)
        conf.write_text(text)
        conf.chmod(0o600)
