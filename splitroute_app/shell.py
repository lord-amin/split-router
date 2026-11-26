from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class ShellResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def combined(self) -> str:
        parts = [p for p in (self.stdout.strip(), self.stderr.strip()) if p]
        return "\n".join(parts)


class PrivilegedCancelledError(Exception):
    pass


class PrivilegedFailedError(Exception):
    pass


def shq(value: str) -> str:
    return shlex.quote(value)


def run_cmd(args: list[str], timeout: float = 10.0) -> ShellResult:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ShellResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return ShellResult(124, "", f"Command timed out: {' '.join(args)}")
    except FileNotFoundError:
        return ShellResult(127, "", f"Executable not found: {args[0]}")
    except Exception as exc:
        return ShellResult(1, "", str(exc))


def run_privileged(commands: list[str], title: str = "", stop_on_error: bool = True) -> str:
    """Runs shell commands as root via pkexec in a single authorization prompt."""
    if not commands:
        return ""
    sep = " && " if stop_on_error else " ; "
    script = sep.join(commands)
    proc = subprocess.run(
        ["pkexec", "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = "\n".join(p for p in (proc.stdout.strip(), proc.stderr.strip()) if p)
    if proc.returncode != 0:
        # pkexec returns 126 when user cancels or 127 if dismissed/auth failed
        lower = out.lower()
        if proc.returncode in (126, 127) and (
            "dismissed" in lower or "not authorized" in lower or "cancelled" in lower or not lower
        ):
            raise PrivilegedCancelledError("Cancelled by user")
        raise PrivilegedFailedError(out or f"Command failed with exit code {proc.returncode}")
    return out

