#!/usr/bin/env python3
"""Rotate era-contracts' ZiSK verifier pins to a zksync-os-zisk release."""

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from lib.entry import run_script
from lib.keccak import keccak256
from lib.script_context import ScriptCtx
import lib.utils as utils
from scripts.update_vk import recompute_hashes_env

ZISK_REPOSITORY = "matter-labs/zksync-os-zisk"
ZISK_UPSTREAM_REPOSITORY = "0xPolygonHermez/zisk"

# Each pin and the zksync-os-zisk file that records it.
PIN_RECORDS = {
    "inner_program_vk": "guest/GUEST_PROGRAM_VK",
    "aggregator_program_vk": "guest-aggregator/GUEST_PROGRAM_VK",
    "root_cv_adcop_final": "guest-aggregator/ROOT_C_VADCOP_FINAL",
}
# The guests' exact `ziskos` requirement names the ZiSK release they build with.
GUEST_MANIFESTS = ("guest/Cargo.toml", "guest-aggregator/Cargo.toml")
UPSTREAM_PLONK_VERIFIER = "zisk-contracts/PlonkVerifier.sol"

VERIFIER_GEN = Path("tools/verifier-gen")
ZISK_VK_JSON = VERIFIER_GEN / "data/ZiSK_vk.json"
PLONK_KEY_JSON = VERIFIER_GEN / "data/ZiSK_plonk_verification_key.json"
ZISK_VERIFIER = Path(
    "l1-contracts/contracts/state-transition/verifiers/ZiskVerifier.sol"
)
RANGE_VECTOR_TEST = Path(
    "l1-contracts/test/foundry/l1/unit/concrete/Verifier/MultiProofRangeVectorTest.t.sol"
)
ALL_CONTRACTS_HASHES = Path("AllContractsHashes.json")
# Everything a rotation may change; the commit step stages the whole checkout.
ROTATED_PATHS = frozenset(
    path.as_posix()
    for path in (ZISK_VK_JSON, ZISK_VERIFIER, RANGE_VECTOR_TEST, ALL_CONTRACTS_HASHES)
)
# MultiProofRangeVectorTest constant -> pin.
RANGE_VECTOR_PINS = {
    "INNER_PROGRAM_VK": "inner_program_vk",
    "AGGREGATOR_PROGRAM_VK": "aggregator_program_vk",
    "ROOT_C_VADCOP_FINAL": "root_cv_adcop_final",
}
PLONK_COMMITMENTS = ("Qm", "Ql", "Qr", "Qo", "Qc", "S1", "S2", "S3")

FIXTURE_SESSION_URL = "https://github.com/matter-labs/zksync-os-zisk/actions/workflows/fixture-session.yaml"

TAG_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")
HEX32_RE = re.compile(r"0x[0-9a-fA-F]{64}")
LIMBS_RE = re.compile(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
SOLIDITY_UINT_RE = re.compile(r"\buint\d+\s+constant\s+(\w+)\s*=\s*(\d+)\s*;")
VK_HASH_RE = re.compile(r"\b_VK_HASH\s*=\s*(0x[0-9a-fA-F]{64})\s*;")

RAW = "application/vnd.github.raw"


def to_limbs(value: bytes) -> list[int]:
    """The four big-endian u64 limbs `ZiSK_vk.json` holds for a pin."""
    if len(value) != 32:
        raise ValueError(f"expected 32 bytes, got {len(value)}")
    return [int.from_bytes(value[i : i + 8], "big") for i in range(0, 32, 8)]


def from_limbs(limbs: list[int]) -> bytes:
    if len(limbs) != 4 or not all(
        type(limb) is int and 0 <= limb < 1 << 64 for limb in limbs
    ):
        raise ValueError(f"expected four u64 limbs, got {limbs!r}")
    return b"".join(limb.to_bytes(8, "big") for limb in limbs)


@dataclass(frozen=True)
class ZiskPins:
    inner_program_vk: bytes
    aggregator_program_vk: bytes
    root_cv_adcop_final: bytes

    def items(self) -> list[tuple[str, bytes]]:
        return [(name, getattr(self, name)) for name in PIN_RECORDS]

    def vk_hash(self) -> bytes:
        """`ZiskVerifier.verificationKeyHash()`."""
        return keccak256(
            self.inner_program_vk
            + self.aggregator_program_vk
            + self.root_cv_adcop_final
        )

    def binding_digest(self, chained_pi: bytes) -> bytes:
        """The digest the aggregated proof carries for a range with `chained_pi`."""
        return keccak256(self.inner_program_vk + self.root_cv_adcop_final + chained_pi)


@dataclass(frozen=True)
class ZiskRelease:
    repository: str
    tag: str
    commit: str
    url: str
    zisk_version: str
    pins: ZiskPins


def parse_pin_record(text: str, label: str) -> bytes:
    """The value of a zksync-os-zisk pin record.

    A record is `#` comments, the value as 0x-prefixed hex, then its history.
    When the comments above the value list its limbs, they must match it.
    """
    header = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header.append(line.lstrip("#").strip())
            continue
        if not HEX32_RE.fullmatch(line):
            raise ValueError(
                f"{label}: expected a 0x-prefixed 32-byte value, found {line!r}"
            )
        value = bytes.fromhex(line[2:])
        recorded = LIMBS_RE.search(" ".join(header))
        if recorded and [int(limb) for limb in recorded.groups()] != to_limbs(value):
            raise ValueError(
                f"{label}: {line} does not match its recorded limbs {recorded.group(0)}"
            )
        return value
    raise ValueError(f"{label}: no value")


def ziskos_version(manifest: str, label: str) -> str:
    """The ZiSK release a guest's Cargo.toml pins `ziskos` to."""
    parsed = tomllib.loads(manifest)
    tables = [parsed.get("dependencies", {})]
    tables += [
        target.get("dependencies", {}) for target in parsed.get("target", {}).values()
    ]
    requirements = {
        table["ziskos"]
        if isinstance(table["ziskos"], str)
        else table["ziskos"].get("version")
        for table in tables
        if "ziskos" in table
    }
    if len(requirements) != 1:
        raise ValueError(
            f"{label}: expected one ziskos requirement, found {sorted(map(str, requirements))}"
        )
    (requirement,) = requirements
    if not (
        isinstance(requirement, str)
        and requirement.startswith("=")
        and VERSION_RE.fullmatch(requirement[1:].strip())
    ):
        raise ValueError(
            f'{label}: expected an exact ziskos requirement ("=X.Y.Z"), found {requirement!r}'
        )
    return requirement[1:].strip()


def zisk_version(manifests: dict[str, str]) -> str:
    versions = {label: ziskos_version(text, label) for label, text in manifests.items()}
    if len(set(versions.values())) != 1:
        raise ValueError(f"the guests pin different ZiSK releases: {versions}")
    return next(iter(versions.values()))


def committed_plonk_constants(key: dict) -> dict[str, int]:
    """The 25 `PlonkVerifier.sol` constants a snarkJS PLONK verification key fixes."""
    if key.get("protocol") != "plonk" or key.get("curve") != "bn128":
        raise ValueError("expected a snarkJS PLONK key over bn128")
    constants = {
        "n": 2 ** int(key["power"]),
        "nPublic": int(key["nPublic"]),
        "w1": int(key["w"]),
        "k1": int(key["k1"]),
        "k2": int(key["k2"]),
    }
    for name in PLONK_COMMITMENTS:
        x, y, z = key[name]
        if z != "1":
            raise ValueError(f"{name} is not in affine form")
        constants[f"{name}x"], constants[f"{name}y"] = int(x), int(y)
    (x1, x2), (y1, y2), z = key["X_2"]
    if z != ["1", "0"]:
        raise ValueError("X_2 is not in affine form")
    constants.update(X2x1=int(x1), X2x2=int(x2), X2y1=int(y1), X2y2=int(y2))
    return constants


def plonk_key_mismatches(key: dict, upstream_source: str) -> list[str]:
    upstream = {
        name: int(value) for name, value in SOLIDITY_UINT_RE.findall(upstream_source)
    }
    mismatches = []
    for name, value in committed_plonk_constants(key).items():
        if name not in upstream:
            mismatches.append(f"{name}: committed {value}, missing upstream")
        elif upstream[name] != value:
            mismatches.append(f"{name}: committed {value}, upstream {upstream[name]}")
    return mismatches


class PlonkKeyMismatch(RuntimeError):
    pass


def check_plonk_key(key: dict, upstream_source: str, version: str) -> int:
    """Fails unless the committed PLONK key equals upstream's; returns the field count."""
    mismatches = plonk_key_mismatches(key, upstream_source)
    if mismatches:
        raise PlonkKeyMismatch(
            f"{PLONK_KEY_JSON} differs from {UPSTREAM_PLONK_VERIFIER} at ZiSK "
            f"v{version}:\n  "
            + "\n  ".join(mismatches)
            + "\nZiSK changed its final PLONK circuit, so the deployed PLONK backend "
            "(zisk_plonk_verifier_addr) has to change too. Rotate the committed key "
            "and deploy a new backend by hand; this workflow only rotates the pins."
        )
    return len(committed_plonk_constants(key))


def gh_api(endpoint: str, accept: str | None = None) -> str:
    command = ["gh", "api", endpoint]
    if accept:
        command += ["-H", f"Accept: {accept}"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"`gh api {endpoint}` failed: {result.stderr.strip()}")
    return result.stdout


def read_release(
    repository: str, tag: str, api: Callable[..., str] = gh_api
) -> ZiskRelease:
    """Reads the pins and the ZiSK version at a published release's commit."""
    try:
        release = json.loads(api(f"repos/{repository}/releases/tags/{tag}"))
    except RuntimeError as error:
        raise RuntimeError(
            f"{tag} is not a published release of {repository}"
        ) from error
    commit = api(
        f"repos/{repository}/commits/{tag}", "application/vnd.github.sha"
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError(f"unexpected commit for {repository}@{tag}: {commit!r}")

    def read(path: str) -> str:
        return api(f"repos/{repository}/contents/{path}?ref={commit}", RAW)

    pins = ZiskPins(
        **{
            name: parse_pin_record(read(path), f"{repository}@{tag}:{path}")
            for name, path in PIN_RECORDS.items()
        }
    )
    version = zisk_version(
        {f"{repository}@{tag}:{path}": read(path) for path in GUEST_MANIFESTS}
    )
    return ZiskRelease(repository, tag, commit, release["html_url"], version, pins)


def write_zisk_vk_json(path: Path, pins: ZiskPins) -> ZiskPins:
    """Writes the pins as limbs and returns the ones the file held."""
    previous = json.loads(path.read_text())
    if list(previous) != list(PIN_RECORDS):
        raise ValueError(
            f"{path}: expected the keys {list(PIN_RECORDS)}, found {list(previous)}"
        )
    contents = {name: to_limbs(value) for name, value in pins.items()}
    path.write_text(json.dumps(contents, indent=2) + "\n")
    return ZiskPins(**{name: from_limbs(previous[name]) for name in PIN_RECORDS})


def _bytes32_constant(name: str) -> re.Pattern:
    return re.compile(
        rf"(\bbytes32\s+internal\s+constant\s+{name}\s*=\s*)(0x[0-9a-fA-F]{{64}})(\s*;)"
    )


def update_range_vector_test(path: Path, pins: ZiskPins) -> bytes:
    """Moves the range vector to the pins and returns its new binding digest."""
    source = path.read_text()
    chained_pi = _bytes32_constant("CHAINED_PI").findall(source)
    if len(chained_pi) != 1:
        raise ValueError(f"{path}: expected one CHAINED_PI, found {len(chained_pi)}")
    digest = pins.binding_digest(bytes.fromhex(chained_pi[0][1][2:]))
    values = {
        constant: getattr(pins, pin) for constant, pin in RANGE_VECTOR_PINS.items()
    }
    values["DIGEST"] = digest
    for name, value in values.items():
        source, count = _bytes32_constant(name).subn(
            rf"\g<1>0x{value.hex()}\g<3>", source
        )
        if count != 1:
            raise ValueError(f"{path}: expected one {name}, found {count}")
    path.write_text(source)
    return digest


def verifier_vk_hash(source: str) -> bytes:
    matches = VK_HASH_RE.findall(source)
    if len(matches) != 1:
        raise ValueError(f"expected one _VK_HASH in ZiskVerifier, found {len(matches)}")
    return bytes.fromhex(matches[0][2:])


def changed_paths(repo_dir: Path) -> set[str]:
    """Tracked paths that differ from HEAD and untracked, unignored paths."""

    def git(*args: str) -> list[str]:
        output = subprocess.run(
            ["git", *args], cwd=repo_dir, check=True, capture_output=True, text=True
        ).stdout
        return [path for path in output.split("\0") if path]

    return set(git("diff", "--name-only", "--no-renames", "-z", "HEAD")) | set(
        git("ls-files", "--others", "--exclude-standard", "-z")
    )


@dataclass(frozen=True)
class Rotation:
    release: ZiskRelease
    previous: ZiskPins
    digest: bytes
    plonk_fields: int

    def manifest(self) -> dict:
        def pins(values: ZiskPins) -> dict:
            return {
                name: {"hex": f"0x{value.hex()}", "limbs": to_limbs(value)}
                for name, value in values.items()
            }

        return {
            "zisk_repository": self.release.repository,
            "zisk_release_tag": self.release.tag,
            "zisk_release_commit": self.release.commit,
            "zisk_version": self.release.zisk_version,
            "pins": pins(self.release.pins),
            "previous_pins": pins(self.previous),
            "vk_hash": f"0x{self.release.pins.vk_hash().hex()}",
            "previous_vk_hash": f"0x{self.previous.vk_hash().hex()}",
            "binding_digest": f"0x{self.digest.hex()}",
            "plonk_key_fields_matching_upstream": self.plonk_fields,
        }


def render_pr_body(rotation: Rotation, run_url: str | None = None) -> str:
    """The era-contracts PR description for a rotation."""
    release = rotation.release
    labels = {
        "inner_program_vk": "inner program VK",
        "aggregator_program_vk": "aggregator program VK",
        "root_cv_adcop_final": "vadcop-final root",
    }
    previous = dict(rotation.previous.items())
    rows = "\n".join(
        f"| {labels[name]} (`{PIN_RECORDS[name]}`) | `0x{value.hex()}` | `0x{previous[name].hex()}` |"
        for name, value in release.pins.items()
    )
    commit_url = f"https://github.com/{release.repository}/commit/{release.commit}"
    provenance = (
        f"Generated by [zksync-os-scripts run]({run_url})"
        if run_url
        else "Generated by zksync-os-scripts `update_zisk_vk.py`"
    )
    return f"""## What ❔

**Goal:** pin the ZiSK range verifier to [zksync-os-zisk release {release.tag}]({release.url}), which builds on ZiSK {release.zisk_version}.

**How it works:** the three pins in `tools/verifier-gen/data/ZiSK_vk.json` move to the values the release commit records, and `ZiskVerifier` is regenerated with `cargo run -- --variant zisk`. The combined VK hash is `0x{release.pins.vk_hash().hex()}` (was `0x{rotation.previous.vk_hash().hex()}`). `MultiProofRangeVectorTest` takes the new pins and the binding digest `keccak256(innerVK ‖ rootC ‖ CHAINED_PI)` = `0x{rotation.digest.hex()}`. All {rotation.plonk_fields} fields of the committed final PLONK key match upstream ZiSK v{release.zisk_version} `{UPSTREAM_PLONK_VERIFIER}`, so the existing PLONK backend stays valid.

| Pin | Value | Previous |
|---|---|---|
{rows}

**Affected contracts:** `ZiskVerifier` (new bytecode; `AllContractsHashes.json` updated).

**Upgrade:** deploy the regenerated `ZiskVerifier` against the existing backend (`zisk_plonk_verifier_addr`). Downstream server manifests must adopt the same pins with release {release.tag}.

**Not updated by the automation:**
- Real-proof fixtures: `ZiskVerifierRealProofTest` still holds proofs for the previous pins. New ones come from a separate run of zksync-os-zisk's [`fixture-session.yaml`]({FIXTURE_SESSION_URL}), which proves on the RTX 6000 runner. CI skips that suite without a PLONK backend.
- The prose that names the release: `l1-contracts/contracts/state-transition/verifiers/README.md` and `MultiProofRangeVectorTest`'s notice.

## Why ❔

L1 must pin the values of the zksync-os-zisk release the provers run, or it rejects their range proofs.

<details>
<summary>Provenance</summary>

{provenance} from [`{release.commit}`]({commit_url}), the commit of tag `{release.tag}`. Where a pin's record lists its limbs, they match the hex value.

</details>

## Checklist

- [x] PR title corresponds to the body of PR (we generate changelog entries from PRs).
- [x] Tests for the changes have been added / updated.
- [ ] Documentation comments have been added / updated.
"""


def script(ctx: ScriptCtx) -> None:
    utils.require_cmds({"cargo": ">=1.89", "gh": ">=2.0"})
    tag = utils.require_env("ZISK_RELEASE_TAG")
    if not TAG_RE.fullmatch(tag):
        raise ValueError(f"Invalid zksync-os-zisk release tag: {tag!r}")
    repository = os.environ.get("ZISK_REPOSITORY") or ZISK_REPOSITORY
    upstream = os.environ.get("ZISK_UPSTREAM_REPOSITORY") or ZISK_UPSTREAM_REPOSITORY

    with ctx.section("Read the release pins", expected=5):
        release = read_release(repository, tag)
        ctx.logger.info("%s@%s is commit %s", repository, tag, release.commit)
        for name, value in release.pins.items():
            ctx.logger.info("%s: 0x%s", name, value.hex())
        ctx.logger.info("ZiSK version: %s", release.zisk_version)

    with ctx.section("Compare the PLONK key with upstream", expected=2):
        upstream_source = gh_api(
            f"repos/{upstream}/contents/{UPSTREAM_PLONK_VERIFIER}"
            f"?ref=v{release.zisk_version}",
            RAW,
        )
        plonk_fields = check_plonk_key(
            json.loads((ctx.repo_dir / PLONK_KEY_JSON).read_text()),
            upstream_source,
            release.zisk_version,
        )

    with ctx.section("Write the pins", expected=1):
        previous = write_zisk_vk_json(ctx.repo_dir / ZISK_VK_JSON, release.pins)
        if previous == release.pins:
            ctx.logger.info("era-contracts already pins %s", tag)

    with ctx.section("Regenerate ZiskVerifier", expected=60):
        ctx.sh(
            [
                "cargo",
                "run",
                "--locked",
                "--",
                "--variant",
                "zisk",
                "--zisk_vk_path",
                "data/ZiSK_vk.json",
                "--zisk_output_path",
                "../../l1-contracts/contracts/state-transition/verifiers/ZiskVerifier.sol",
            ],
            cwd=ctx.repo_dir / VERIFIER_GEN,
        )
        generated = verifier_vk_hash((ctx.repo_dir / ZISK_VERIFIER).read_text())
        if generated != release.pins.vk_hash():
            raise RuntimeError(
                f"ZiskVerifier's VK hash 0x{generated.hex()} differs from "
                f"keccak256 over the pins, 0x{release.pins.vk_hash().hex()}"
            )

    with ctx.section("Update the range vector", expected=1):
        digest = update_range_vector_test(
            ctx.repo_dir / RANGE_VECTOR_TEST, release.pins
        )
        ctx.logger.info("Binding digest: 0x%s", digest.hex())

    with ctx.section("Update test hashes", expected=300):
        ctx.sh(
            ["bash", str(ctx.repo_dir / "recompute_hashes.sh")],
            env=recompute_hashes_env(),
        )

    with ctx.section("Check the changed files", expected=1):
        unexpected = sorted(changed_paths(ctx.repo_dir) - ROTATED_PATHS)
        if unexpected:
            raise RuntimeError(
                "The rotation changed files outside "
                f"{sorted(ROTATED_PATHS)}: {unexpected}"
            )

    rotation = Rotation(release, previous, digest, plonk_fields)
    (ctx.workspace / "zisk-rotation.json").write_text(
        json.dumps(rotation.manifest(), indent=2) + "\n"
    )
    (ctx.workspace / "zisk_pr_body.md").write_text(
        render_pr_body(rotation, os.environ.get("RUN_URL"))
    )


if __name__ == "__main__":
    run_script(script)
