import sys
from pathlib import Path
import logging

from rich.console import Console
from rich.logging import RichHandler

# One shared console
_console = Console(file=sys.stdout)

def get_console() -> Console:
    return _console

def setup_logger(name: str, log_file: Path | None, verbose: bool) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # Console handler (Rich)
    console = RichHandler(
        rich_tracebacks=True,
        markup=True,
        show_path=False,
        show_time=False,
        console=_console,
        show_level=False
    )
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(console)

    # File handler
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

    return logger
