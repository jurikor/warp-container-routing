"""Core command execution and validation helpers."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import shlex
import shutil
import subprocess
from typing import Iterable


class AppError(RuntimeError):
    """Expected user-facing error."""


@dataclasses.dataclass
class CommandResult:
    """Result of a host command executed through CommandRunner."""

    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    """Small subprocess wrapper with consistent dry-run, verbose and error handling."""

    def __init__(self, dry_run: bool = False, verbose: bool = False) -> None:
        """Create a runner that can either execute commands or only print them."""

        self.dry_run = dry_run
        self.verbose = verbose

    def run(
        self,
        args: Iterable[str],
        *,
        check: bool = True,
        capture: bool = False,
        input_text: str | None = None,
        cwd: str | pathlib.Path | None = None,
    ) -> CommandResult:
        """Run a command and optionally capture output or raise AppError on failure."""

        cmd = [str(arg) for arg in args]
        if self.verbose or self.dry_run:
            print("+ " + shlex.join(cmd))
        if self.dry_run:
            return CommandResult(cmd, 0, "", "")

        completed = subprocess.run(
            cmd,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if check and completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            if detail:
                raise AppError(f"command failed: {shlex.join(cmd)}\n{detail}")
            raise AppError(f"command failed: {shlex.join(cmd)}")
        return CommandResult(cmd, completed.returncode, stdout, stderr)

    def stdout(self, args: Iterable[str], *, check: bool = True) -> str:
        """Run a command and return stdout as text."""

        return self.run(args, check=check, capture=True).stdout

    def exists(self, binary: str) -> bool:
        """Return whether a binary is available in PATH."""

        return shutil.which(binary) is not None


def require_root() -> None:
    """Fail early unless the current process has root privileges."""

    if os.geteuid() != 0:
        raise AppError("run as root")


def require_commands(commands: Iterable[str]) -> None:
    """Fail if any required host command is missing from PATH."""

    missing = [cmd for cmd in commands if shutil.which(cmd) is None]
    if missing:
        raise AppError("missing required command(s): " + ", ".join(missing))
