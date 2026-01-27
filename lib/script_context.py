import contextlib
import logging
import os
import shlex
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Iterable, Mapping, Optional, Union
from lib.log import get_console
from lib import constants


logger = logging.getLogger(constants.LOGGER_NAME)
_console = get_console()


@dataclass(slots=True)
class ScriptCtx:
    # Core identity
    workspace: Path
    repo_dir: Path
    script_name: str
    component: str

    # Flags
    verbose: bool

    # Logging
    log_file: Optional[Path]
    logger: logging.Logger

    # Section summary counters
    sections_total: int = field(default=0, init=False)
    sections_ok: int = field(default=0, init=False)
    sections_failed: int = field(default=0, init=False)

    # ------------------------------------------------------------------ #
    # Sections
    # ------------------------------------------------------------------ #

    @contextlib.contextmanager
    def section(self, title: str, expected: float | None = None):
        """
        Context manager: section with spinner + timing and optional expected time.

        Example:
            with ctx.section("Build wrapper", expected=20):
                ctx.sh("cargo run --release --bin wrapper_generator")
        """
        self.sections_total += 1
        label = f"{title} (≈{expected:.0f}s)" if expected is not None else title
        # Header
        self.logger.info(f"\n[bold][{self.sections_total}] {label}[/]")
        start = perf_counter()
        # Spinner while the block runs
        with _console.status(f"[cyan]{label}...[/]"):
            try:
                yield
            except Exception:
                duration = perf_counter() - start
                self.sections_failed += 1
                self._log_section_result(title, duration, expected, success=False)
                raise
            else:
                duration = perf_counter() - start
                self.sections_ok += 1
                self._log_section_result(title, duration, expected, success=True)

    def _log_section_result(
        self,
        title: str,
        duration: float,
        expected: float | None,
        *,
        success: bool,
    ) -> None:
        msg = f"{title}: {'SUCCESS' if success else 'FAILED'} ({duration:.1f}s"
        if expected is not None and expected > 0:
            delta = duration - expected
            sign = "+" if delta > 0 else "−"
            msg += f", {sign}{abs(delta):.1f}s vs expected {expected:.1f}s"
        msg += ")"

        if success:
            self.logger.info(msg)
        else:
            self.logger.error(msg)

    # ------------------------------------------------------------------ #
    # Paths / env
    # ------------------------------------------------------------------ #

    def sh(
        self,
        cmd: Union[str, Iterable[str]],
        *,
        cwd: Optional[Path | str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        """
        Run a command safely.
        - If cmd is a string, it is split shell-style (no shell=True).
        - If cmd is an iterable, it is used directly as argv.
        """
        # Normalize command → argv
        if isinstance(cmd, str):
            argv = shlex.split(cmd)
        else:
            argv = list(cmd)

        cwd_path = Path(cwd) if cwd is not None else self.repo_dir

        # ❗ Validate cwd
        if not cwd_path.exists():
            self.logger.error(f"Working directory does not exist: {cwd_path}")
            raise SystemExit(1)
        if not cwd_path.is_dir():
            self.logger.error(f"Working directory is not a directory: {cwd_path}")
            raise SystemExit(1)

        # Log command
        self.logger.info("$ " + " ".join(argv))

        # Prepare env
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        # Stream output
        level = logging.INFO if self.verbose else logging.DEBUG
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd_path),
                env=merged_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            self.logger.error(f"Executable not found when running: {argv[0]!r}")
            raise SystemExit(1)

        assert proc.stdout is not None
        for line in proc.stdout:
            self.logger.log(level, line.rstrip("\n"))

        rc = proc.wait()
        if rc != 0:
            self.logger.error(f"command in {cwd_path} failed with exit code {rc}")
            self._tail_last_lines()
            raise subprocess.CalledProcessError(rc, argv)

    def _tail_last_lines(self, n: int = 20) -> None:
        if not self.log_file or not self.log_file.exists():
            return
        print(f"── last {n} log lines ─────────────────────────")
        with self.log_file.open(encoding="utf-8") as f:
            for line in deque(f, maxlen=n):
                sys.stdout.write(line)
        print("──────────────────────────────────────────────")
