import contextlib
import re
import shutil
import os
import subprocess
import time
import urllib.request
from pathlib import Path
import yaml
from packaging.version import Version

def require_env(name: str, default: str = None) -> str:
    """
    Return the value of a required environment variable or exit with
    a clear error message if it's missing or empty.
    """
    val = os.environ.get(name, default)
    if not val:
        # Keep it simple; run_main will not even start if this fails.
        raise SystemExit(f"Missing required environment variable: {name}")
    return val

def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clean_dir(path: Path) -> Path:
    """
    Remove a directory if it exists and recreate it empty.

    Returns the resulting directory Path.
    """
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path

def remove_dir(path: Path) -> Path:
    """
    Remove a directory if it exists.

    Returns the resulting directory Path.
    """
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    return path

def cp(src: Path, dst: Path) -> None:
    """Copy a file"""
    if not src.exists():
        raise FileNotFoundError(f"Required file does not exist: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if not dst.exists():
        raise FileNotFoundError(f"File not found after copy: {dst}")

def download(url: str, dest: Path, force: bool = False) -> None:
    """Minimal, safe downloader"""
    if dest.exists() and not force:
        return
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url) as r:
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status} for URL: {url}")
            with tmp.open("wb") as f:
                shutil.copyfileobj(r, f)  # efficient streaming copy
        tmp.replace(dest)  # atomic move
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

def extract_vk_hash(verifier_plonk_sol: Path) -> str:
    """
    Reads a .sol verifier file and extracts the VK hash from its @dev comment.
    Example of line:
        /// @dev Contract was generated from a verification key with a hash of 0x....

    If output_path is provided (or default None), it writes to .tmp/vk_hash.txt.
    Returns the hash string.
    """
    vk_hash_re = re.compile(
        r"hash of (0x[0-9a-fA-F]{64})"
    )
    if not verifier_plonk_sol.is_file():
        raise FileNotFoundError(f"Verifier file not found: {verifier_plonk_sol}")
    content = verifier_plonk_sol.read_text(encoding="utf-8")
    match = vk_hash_re.search(content)
    if not match:
        raise RuntimeError(f"Could not find VK hash in {verifier_plonk_sol}")
    vk_hash = match.group(1)
    return vk_hash

def get_cmd_version(cmd: str) -> Version:
    _VERSION_RE = re.compile(r"\d+\.\d+(\.\d+)?")
    result = subprocess.run(
        [cmd, "--version"],
        capture_output=True,
        text=True,
        check=True,
    )

    match = _VERSION_RE.search(result.stdout)
    if not match:
        raise RuntimeError(
            f"Could not parse version from `{cmd} --version`: {result.stdout}"
        )

    return Version(match.group())

def normalize_hex(value: str | int, length: int | None = None) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int):
        if length is None:
            return f"0x{value:x}"
        return f"0x{value:0{length}x}"
    raise TypeError(f"Expected str or int, got {type(value)}")

@contextlib.contextmanager
def anvil_dump_state(
        *,
        l1_state_file: Path,
):
    """
    Run Anvil (with --dump-state) for the duration of the block.

    Usage:
        with ctx.anvil_dump_state(l1_state_file=server_l1_state_file):
            ... do stuff while Anvil is running ...
    """
    proc = subprocess.Popen(
        [
            "anvil",
            "--dump-state",
            str(l1_state_file),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    time.sleep(2)

    if proc.poll() not in (None, 0):
        raise SystemExit(
            f"Failed to start Anvil (exit {proc.returncode}); "
            f"state file: {l1_state_file}"
        )

    try:
        yield proc
    finally:
        pid = proc.pid
        if proc.poll() is not None:
            print(f"Anvil already stopped (pid={pid})")
            return

        print(f"Stopping Anvil (pid={pid})")
        try:
            proc.terminate()
        except Exception:
            pass

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Anvil still alive; sending SIGKILL")
            try:
                proc.kill()
            except Exception:
                pass
            _ = proc.wait(timeout=5)

def addresses_from_wallets_yaml(data: dict) -> set[str]:
    """
    wallets.yaml format:
        name:
          address: 0x...
          private_key: 0x...

    Return all address values as 0x-prefixed hex strings.
    Handles both YAML strings and ints (0x... parsed as int).
    """
    addrs: set[str] = set()

    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue

        addr = entry.get("address")
        if isinstance(addr, str):
            addrs.add(addr.strip())
        elif isinstance(addr, int):
            # Ethereum address: 20 bytes → 40 hex chars
            addrs.add(f"0x{addr:040x}")

    return addrs
