#!/usr/bin/env python3
"""Rotate era-contracts' ZiSK verifier pins to a zksync-os-zisk release."""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
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
# The final SNARK verifier of the lane's Poseidon1 family, which era-contracts vendors.
UPSTREAM_SNARK_VERIFIER = "zisk-contracts/poseidon1/PilfflonkVerifier.sol"

VERIFIER_GEN = Path("tools/verifier-gen")
ZISK_VK_JSON = VERIFIER_GEN / "data/ZiSK_vk.json"
ZISK_VERIFIER = Path(
    "l1-contracts/contracts/state-transition/verifiers/ZiskVerifier.sol"
)
SNARK_VERIFIER = Path(
    "l1-contracts/contracts/state-transition/verifiers/PilfflonkVerifier.sol"
)
RANGE_VECTOR_TEST = Path(
    "l1-contracts/test/foundry/l1/unit/concrete/Verifier/MultiProofRangeVectorTest.t.sol"
)
REAL_PROOF_TEST = Path(
    "l1-contracts/test/foundry/l1/unit/concrete/Verifier/ZiskVerifierRealProofTest.t.sol"
)
ALL_CONTRACTS_HASHES = Path("AllContractsHashes.json")
# Everything a rotation may change; the commit step stages the whole checkout.
ROTATED_PATHS = frozenset(
    path.as_posix()
    for path in (
        ZISK_VK_JSON,
        ZISK_VERIFIER,
        RANGE_VECTOR_TEST,
        REAL_PROOF_TEST,
        ALL_CONTRACTS_HASHES,
    )
)
# MultiProofRangeVectorTest constant -> pin.
RANGE_VECTOR_PINS = {
    "INNER_PROGRAM_VK": "inner_program_vk",
    "AGGREGATOR_PROGRAM_VK": "aggregator_program_vk",
    "ROOT_C_VADCOP_FINAL": "root_cv_adcop_final",
}

FIXTURE_SESSION_WORKFLOW = "fixture-session.yaml"
FIXTURE_SESSION_URL = (
    f"https://github.com/{ZISK_REPOSITORY}/actions/workflows/{FIXTURE_SESSION_WORKFLOW}"
)
# The artifact holding a session's proofs, and the files in it this script reads.
FIXTURE_SESSION_ARTIFACT = "zisk-fixture-session"
BATCH_INSPECT = "batch1-inspect.txt"
AGGREGATED_INSPECT = "aggregated-inspect.txt"
# How many of the newest successful sessions to search. A session keeps its
# artifact for 14 days, so older ones have nothing left to download.
FIXTURE_SESSION_LOOKBACK = 20

# A ZiSK final SNARK and its public values:
# programVK(32) || 64 guest-public u64 slots(512) || rootCVadcopFinal(32).
SNARK_PROOF_BYTES = 2208
PUBLIC_VALUES_BYTES = 576
GUEST_PUBLICS_BYTES = 512

TAG_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")
HEX32_RE = re.compile(r"0x[0-9a-fA-F]{64}")
LIMBS_RE = re.compile(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
VK_HASH_RE = re.compile(r"\b_VK_HASH\s*=\s*(0x[0-9a-fA-F]{64})\s*;")
# `inspect_proof` output lines, e.g. `PROOF(2208): <hex>`.
INSPECT_RE = re.compile(r"^(PROOF|PUBLIC_VALUES)\((\d+)\): ([0-9a-f]*)$", re.MULTILINE)

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


class SnarkVerifierMismatch(RuntimeError):
    pass


def check_snark_verifier(vendored: str, upstream: str, version: str) -> str:
    """Fails unless the vendored pilfflonk verifier is upstream's file.

    The verifier hardcodes ZiSK's final SNARK key, so a ZiSK release that changes
    it needs a new vendored copy and a new deployment. Returns the file's SHA-256.
    """
    if vendored != upstream:
        raise SnarkVerifierMismatch(
            f"{SNARK_VERIFIER} differs from {UPSTREAM_SNARK_VERIFIER} at ZiSK "
            f"v{version}. ZiSK changed its final SNARK verifier, so the deployed "
            "PilfflonkVerifier has to change too. Vendor the new file and deploy it "
            "by hand; this workflow only rotates the pins."
        )
    return hashlib.sha256(vendored.encode()).hexdigest()


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


@dataclass(frozen=True)
class SnarkFixture:
    proof: bytes
    public_values: bytes


@dataclass(frozen=True)
class FixtureSession:
    run_url: str
    batch: SnarkFixture
    aggregated: SnarkFixture


def expected_public_values(program_vk: bytes, value: bytes, root_c: bytes) -> bytes:
    """The public values of a SNARK whose guest commits `value` and nothing else.

    A guest public holds 32 bits, so `value` fills the first eight u64 slots four
    bytes at a time, each followed by four zero pad bytes; the other slots are zero.
    """
    slots = b"".join(value[i : i + 4] + bytes(4) for i in range(0, 32, 4))
    return program_vk + slots.ljust(GUEST_PUBLICS_BYTES, b"\0") + root_c


def parse_inspect(text: str, label: str) -> SnarkFixture:
    """The proof and public values in zksync-os-zisk `inspect_proof` output."""
    values = {}
    for kind, length, value in INSPECT_RE.findall(text):
        if kind in values:
            raise ValueError(f"{label}: more than one {kind} line")
        data = bytes.fromhex(value)
        if len(data) != int(length):
            raise ValueError(f"{label}: {kind} holds {len(data)} bytes, not {length}")
        values[kind] = data
    if set(values) != {"PROOF", "PUBLIC_VALUES"}:
        raise ValueError(f"{label}: expected one PROOF and one PUBLIC_VALUES line")
    fixture = SnarkFixture(values["PROOF"], values["PUBLIC_VALUES"])
    if len(fixture.proof) != SNARK_PROOF_BYTES:
        raise ValueError(f"{label}: expected a {SNARK_PROOF_BYTES}-byte proof")
    if len(fixture.public_values) != PUBLIC_VALUES_BYTES:
        raise ValueError(
            f"{label}: expected {PUBLIC_VALUES_BYTES} bytes of public values"
        )
    return fixture


def download_fixture_session(repository: str, run_id: int) -> dict[str, str]:
    """The inspect files of a fixture session's artifact; missing ones are left out."""
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            ["gh", "run", "download", str(run_id), "--repo", repository]
            + ["--name", FIXTURE_SESSION_ARTIFACT, "--dir", directory],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`gh run download {run_id}` failed: {result.stderr.strip()}"
            )
        files = {}
        for name in (BATCH_INSPECT, AGGREGATED_INSPECT):
            path = Path(directory) / name
            if path.is_file():
                files[name] = path.read_text()
        return files


def find_fixture_session(
    repository: str,
    pins: ZiskPins,
    commitment: bytes,
    digest: bytes,
    logger: logging.Logger,
    api: Callable[..., str] = gh_api,
    download: Callable[[str, int], dict[str, str]] = download_fixture_session,
) -> FixtureSession | None:
    """The newest successful fixture session that proved these pins.

    A session qualifies only if its batch-1 public values are exactly the inner
    programVK, `commitment` and the root, and its aggregated ones exactly the
    aggregator programVK, `digest` and the root. The proofs themselves are
    checked by `ZiskVerifierRealProofTest`, which runs the real pairing.
    """
    expected = {
        BATCH_INSPECT: expected_public_values(
            pins.inner_program_vk, commitment, pins.root_cv_adcop_final
        ),
        AGGREGATED_INSPECT: expected_public_values(
            pins.aggregator_program_vk, digest, pins.root_cv_adcop_final
        ),
    }
    runs = json.loads(
        api(
            f"repos/{repository}/actions/workflows/{FIXTURE_SESSION_WORKFLOW}/runs"
            f"?status=success&per_page={FIXTURE_SESSION_LOOKBACK}"
        )
    )["workflow_runs"]
    for run in runs:
        artifacts = json.loads(
            api(
                f"repos/{repository}/actions/runs/{run['id']}/artifacts"
                f"?name={FIXTURE_SESSION_ARTIFACT}"
            )
        )["artifacts"]
        if not any(not artifact["expired"] for artifact in artifacts):
            continue
        files = download(repository, run["id"])
        try:
            fixtures = {
                name: parse_inspect(files[name], f"{run['html_url']}: {name}")
                for name in expected
            }
        except (KeyError, ValueError) as error:
            logger.info("Skipping %s: %s", run["html_url"], error)
            continue
        if all(
            fixtures[name].public_values == public_values
            for name, public_values in expected.items()
        ):
            return FixtureSession(
                run["html_url"], fixtures[BATCH_INSPECT], fixtures[AGGREGATED_INSPECT]
            )
        logger.info("Skipping %s: it proved other values", run["html_url"])
    return None


def real_proof_commitment(path: Path) -> bytes:
    """The batch-1 commitment `ZiskVerifierRealProofTest`'s range starts with."""
    matches = _bytes32_constant("COMMITMENT_1").findall(path.read_text())
    if len(matches) != 1:
        raise ValueError(f"{path}: expected one COMMITMENT_1, found {len(matches)}")
    return bytes.fromhex(matches[0][1][2:])


def update_real_proof_test(
    path: Path, session: FixtureSession, repository: str, tag: str
) -> None:
    """Replaces the test's two SNARKs, their public values and the run it cites."""
    source = path.read_text()
    for name, value in {
        "BATCH_PROOF": session.batch.proof,
        "BATCH_PUBLIC_VALUES": session.batch.public_values,
        "AGGREGATED_PROOF": session.aggregated.proof,
        "AGGREGATED_PUBLIC_VALUES": session.aggregated.public_values,
    }.items():
        pattern = re.compile(
            rf'(\bbytes\s+internal\s+constant\s+{name}\s*=\s*hex")[0-9a-fA-F]*(")'
        )
        source, count = pattern.subn(rf"\g<1>{value.hex()}\g<2>", source)
        if count != 1:
            raise ValueError(f"{path}: expected one {name}, found {count}")
    run_url = re.compile(
        rf"https://github\.com/{re.escape(repository)}/actions/runs/\d+"
    )
    source, count = run_url.subn(session.run_url, source)
    if count != 1:
        raise ValueError(f"{path}: expected one fixture-session run URL, found {count}")
    # The notice may name the release the fixtures were proved for.
    source = re.sub(
        r"(reproduced from the release )[0-9A-Za-z._-]+( pins)",
        rf"\g<1>{tag}\g<2>",
        source,
        count=1,
    )
    path.write_text(source)


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
    snark_verifier_sha256: str
    # The fixture session `ZiskVerifierRealProofTest` now cites; None if none matched.
    fixture_session_url: str | None = None

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
            "snark_verifier_sha256": self.snark_verifier_sha256,
            "fixture_session_run_url": self.fixture_session_url,
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
    if rotation.fixture_session_url:
        fixtures = f" `ZiskVerifierRealProofTest` takes the batch-1 and aggregated pilfflonk proofs of [fixture session {rotation.fixture_session_url.rsplit('/', 1)[-1]}]({rotation.fixture_session_url}), the newest successful session whose public values are exactly these pins, the test's `COMMITMENT_1` and the binding digest; the test checks the proofs with the real pairing."
        not_updated = ""
    else:
        fixtures = ""
        not_updated = f"""
- Real-proof fixtures: none of the last {FIXTURE_SESSION_LOOKBACK} successful runs of zksync-os-zisk's [`fixture-session.yaml`]({FIXTURE_SESSION_URL}) has an artifact that proves these pins (artifacts last 14 days). `ZiskVerifierRealProofTest` still holds the previous release's proofs, so it fails until a fixture session for this release (RTX 6000 runner) replaces them."""
    return f"""## What ❔

**Goal:** pin the ZiSK range verifier to [zksync-os-zisk release {release.tag}]({release.url}), which builds on ZiSK {release.zisk_version}.

**How it works:** the three pins in `tools/verifier-gen/data/ZiSK_vk.json` move to the values the release commit records, and `ZiskVerifier` is regenerated with `cargo run -- --variant zisk`. The combined VK hash is `0x{release.pins.vk_hash().hex()}` (was `0x{rotation.previous.vk_hash().hex()}`). `MultiProofRangeVectorTest` takes the new pins and the binding digest `keccak256(innerVK ‖ rootC ‖ CHAINED_PI)` = `0x{rotation.digest.hex()}`.{fixtures} The vendored `PilfflonkVerifier.sol` is byte-identical to upstream ZiSK v{release.zisk_version} `{UPSTREAM_SNARK_VERIFIER}` (SHA-256 `{rotation.snark_verifier_sha256}`), so the deployed SNARK verifier stays valid.

| Pin | Value | Previous |
|---|---|---|
{rows}

**Affected contracts:** `ZiskVerifier` (new bytecode; `AllContractsHashes.json` updated).

**Chain states:** the new bytecode changes the anvil-interop chain states. After opening this PR, the rotation workflow dispatches *Update All Generated Artifacts* for it, which commits the regenerated `l1-contracts/test/anvil-interop/chain-states/`. `state-generation-check` fails until that commit lands.

**Upgrade:** deploy the regenerated `ZiskVerifier` (the range verifier, `zisk_range_verifier_addr`) with the existing `PilfflonkVerifier` as its `IZiskSnarkVerifier`. Downstream server manifests must adopt the same pins with release {release.tag}.

**Not updated by the automation:**{not_updated}
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

    with ctx.section("Compare the SNARK verifier with upstream", expected=2):
        upstream_source = gh_api(
            f"repos/{upstream}/contents/{UPSTREAM_SNARK_VERIFIER}"
            f"?ref=v{release.zisk_version}",
            RAW,
        )
        snark_verifier_sha256 = check_snark_verifier(
            (ctx.repo_dir / SNARK_VERIFIER).read_text(),
            upstream_source,
            release.zisk_version,
        )
        ctx.logger.info("PilfflonkVerifier.sol SHA-256: %s", snark_verifier_sha256)

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

    with ctx.section("Update the real-proof fixtures", expected=60):
        session = find_fixture_session(
            repository,
            release.pins,
            real_proof_commitment(ctx.repo_dir / REAL_PROOF_TEST),
            digest,
            ctx.logger,
        )
        if session is None:
            ctx.logger.warning(
                "No fixture session proves %s; %s keeps its proofs and fails until "
                "they are replaced",
                tag,
                REAL_PROOF_TEST.name,
            )
        else:
            update_real_proof_test(
                ctx.repo_dir / REAL_PROOF_TEST, session, repository, tag
            )
            ctx.logger.info("Real-proof fixtures: %s", session.run_url)

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

    rotation = Rotation(
        release,
        previous,
        digest,
        snark_verifier_sha256,
        session.run_url if session else None,
    )
    (ctx.workspace / "zisk-rotation.json").write_text(
        json.dumps(rotation.manifest(), indent=2) + "\n"
    )
    (ctx.workspace / "zisk_pr_body.md").write_text(
        render_pr_body(rotation, os.environ.get("RUN_URL"))
    )


if __name__ == "__main__":
    run_script(script)
