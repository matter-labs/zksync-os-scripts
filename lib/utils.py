import hashlib
import gzip
import contextlib
import re
import shutil
import os
import subprocess
import time
from typing import Optional
import urllib.request
from pathlib import Path
import yaml
from shutil import which
from packaging.specifiers import SpecifierSet
from packaging.version import Version
import logging
from lib import constants


logger = logging.getLogger(constants.LOGGER_NAME)


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


def require_path(env_var: str) -> Path:
    """
    Return a path resolved from an environment variable or a default inside the workspace.

    Example:
        utils.require_path("REPO_PATH")
    """
    val = os.environ.get(env_var)
    if not val:
        raise SystemExit(f"Missing required path: {env_var}")
    return Path(val).resolve()


def require_cmds(tools: dict[str, str]) -> None:
    """
    Ensure required command-line tools are available with correct versions.
    """
    missing = [t for t in tools if which(t) is None]
    if missing:
        raise SystemExit(f"Missing required tools: {', '.join(missing)}")
    for tool, constraint in tools.items():
        version = get_cmd_version(tool)
        spec = SpecifierSet(constraint)
        if version not in spec:
            raise SystemExit(
                f"{tool} {version} does not satisfy required version {constraint}"
            )
        logger.info(f"Found {tool} {version} ✔")


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


def download(
    url: str,
    dest: Path,
    force: bool = False,
    checksum: Optional[str] = None,
    checksum_algo: str = "sha256",
) -> None:
    """Download file with optional checksum verification"""
    dest = Path(dest)

    if dest.exists() and not force:
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    hasher = hashlib.new(checksum_algo) if checksum else None

    try:
        with urllib.request.urlopen(url) as r:
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status} for URL: {url}")

            with tmp.open("wb") as f:
                while True:
                    chunk = r.read(1024 * 1024)  # 1 MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    if hasher:
                        hasher.update(chunk)

        if hasher:
            digest = hasher.hexdigest()
            if digest.lower() != checksum.lower():
                raise RuntimeError(
                    f"Checksum mismatch for {url}: expected {checksum}, got {digest}"
                )

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
    vk_hash_re = re.compile(r"hash of (0x[0-9a-fA-F]{64})")
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


def gzip_file(src: Path, *, dst: Path | None = None, keep_src: bool = False) -> Path:
    """Gzip a file with optional compression level and option to keep the source file."""
    if dst is None:
        dst = src.with_suffix(f"{src.suffix}.gz")  # e.g. state.json -> state.json.gz
    tmp = dst.with_suffix(f"{dst.suffix}.tmp")
    # Stream-compress to a temp file, then atomically replace.
    with src.open("rb") as fin, gzip.open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    tmp.replace(dst)
    if not keep_src:
        try:
            src.unlink()
        except FileNotFoundError:
            pass
    return dst


@contextlib.contextmanager
def anvil_dump_state(
    *,
    l1_state_file: Path,
    compress: bool = True,
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
            logger.debug(f"Anvil already stopped (pid={pid})")
        else:
            logger.debug(f"Stopping Anvil (pid={pid})")
            try:
                proc.terminate()
            except Exception:
                pass

            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.debug("Anvil still alive; sending SIGKILL")
                try:
                    proc.kill()
                except Exception:
                    pass
                _ = proc.wait(timeout=5)

        # Compress after anvil dumped the state file
        if compress and l1_state_file.exists():
            gz_path = gzip_file(l1_state_file, keep_src=False)
            logger.info(f"Compressed state -> {gz_path}")


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


def replace_with_symlink(target: Path, source: Path) -> None:
    """
    Replace target with a symlink to source.
    If target exists, it is removed first.
    """
    if target == source:
        return
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    target.symlink_to(source, target_is_directory=source.is_dir())


def parse_protocol_version(version: str) -> tuple[int, int]:
    """
    Parse a protocol version string like 'v31.0' into a (minor, patch) tuple.

    Args:
        version: Version string in format 'vX.Y' where X and Y are non-negative integers.

    Returns:
        Tuple of (minor, patch) integers.

    Raises:
        ValueError: If the version string is empty, malformed, or contains invalid values.
    """
    if not version or not isinstance(version, str):
        raise ValueError(
            f"Protocol version must be a non-empty string, got: {version!r}"
        )

    version = version.strip()
    if not re.match(r"^v\d+\.\d+$", version):
        raise ValueError(
            f"Invalid protocol version format: '{version}'. Expected format: 'vX.Y' (e.g., 'v31.0')"
        )

    # Remove the 'v' prefix and split
    minor_str, patch_str = version[1:].split(".")
    return (int(minor_str), int(patch_str))
