import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import yaml

from lib.keccak import keccak256
from scripts.update_zisk_vk import (
    AGGREGATED_INSPECT,
    BATCH_INSPECT,
    RANGE_VECTOR_TEST,
    REAL_PROOF_TEST,
    SNARK_VERIFIER,
    ZISK_VERIFIER,
    ZISK_VK_JSON,
    FixtureSession,
    Rotation,
    SnarkFixture,
    SnarkVerifierMismatch,
    ZiskPins,
    ZiskRelease,
    check_snark_verifier,
    expected_public_values,
    find_fixture_session,
    from_limbs,
    parse_inspect,
    parse_pin_record,
    read_release,
    real_proof_commitment,
    render_pr_body,
    script,
    to_limbs,
    update_range_vector_test,
    update_real_proof_test,
    write_zisk_vk_json,
    zisk_version,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/update-zisk-vk.yaml"

# Stands in for era-contracts' vendored PilfflonkVerifier.sol; the check compares
# the whole file with upstream's.
PILFFLONK_VERIFIER = """// SPDX-License-Identifier: MIT OR Apache-2.0
pragma solidity >=0.7.0 <0.9.0;

contract PilfflonkVerifier {
    uint256 constant C0x = 1;
}
"""

# zksync-os-zisk release 0.0.9, as era-contracts#2622 pins it.
PINS_0_0_9 = ZiskPins(
    bytes.fromhex("09ebc4f2e17b20d69b6f2850ddfe187ba5b0514abffbcf789823cc6e4e44ef02"),
    bytes.fromhex("9e2ea93a5e08c2ddb5b93323a593ada955dc74d00fac433d45c97da5b90e5e08"),
    bytes.fromhex("6b2490fba336fb00f0c8719a961de309a8db6f43a9bfb7575ca5f014d609b265"),
)
# Release 0.0.8, from the unmerged era-contracts#2583.
PINS_0_0_8 = ZiskPins(
    bytes.fromhex("93172dbe40432534d5ad84e95b3d2324c6e9a28c1fbd6b770776e4c8d31ec4b9"),
    bytes.fromhex("558aff7c3342a2636ea8aa9f91d151d0300b7aeccd43d682ac854a79548cb1a7"),
    bytes.fromhex("c3f12b9f8707c6a1e96df2bf6702c2ebdfbafedabeac654644a380befe091ac4"),
)
# MultiProofRangeVectorTest's frozen range public input.
CHAINED_PI = bytes.fromhex(
    "00000000108311cf154dafcd8fbeb3d29ff924941d60db59f523d33baa5d2ca5"
)
DIGEST_0_0_9 = bytes.fromhex(
    "ec1bf9cd08fde7ce771e6ceb96064b0fa73b3a475e7f6da8675989c3c40b13a2"
)
VK_HASH_0_0_9 = bytes.fromhex(
    "482778c1f7c2f1153a627d52a592d6d44d8dda440ddae77dc101e31251f49197"
)
# Release 0.0.10 (ZiSK 1.4.0-alpha, pilfflonk), as era-contracts#2657 pins it, with
# the VK hash of its ZiskVerifier and the DIGEST of its MultiProofRangeVectorTest.
PINS_0_0_10 = ZiskPins(
    bytes.fromhex("747be84de38d60d9b5816ddad433d41c9771825b630bddbc90cb1585dda69a6c"),
    bytes.fromhex("e7f72bf4c1825a6f852e65fc872c810320218c63ed41cf9bb4345737a4ac31ec"),
    bytes.fromhex("e9414e134bb6945a148a79d44c8e850e5ef582a2b0e8dbfb2428ff0a515a6e66"),
)
VK_HASH_0_0_10 = bytes.fromhex(
    "38afdbde9dffe0a1ef942e7214d59700b47319d225d4558bb6f166ef09a31dd7"
)
DIGEST_0_0_10 = bytes.fromhex(
    "5afe2e89a27ddd841b343cff7fb3a9f3c0dc18b0d6bc7d685fbfa55af59c9bd0"
)
# Release 0.0.11, as era-contracts#2709 pins it.
PINS_0_0_11 = ZiskPins(
    bytes.fromhex("2ec490cc1239f924eddc97fa13db1e4eee4124cdd85a15a1f518990d54f652d8"),
    PINS_0_0_10.aggregator_program_vk,
    PINS_0_0_10.root_cv_adcop_final,
)
VK_HASH_0_0_11 = bytes.fromhex(
    "db14dd4e90244a5924f1cb2c59149dfcf945939f10440c85880b73dcf0473c36"
)
DIGEST_0_0_11 = bytes.fromhex(
    "b1449f75ebc58ce20243dd3774c8f1a617b4e06df533c7e74c2e8cedc7bfb0b0"
)
# The first batch of the frozen fixture range; ZiskVerifierRealProofTest's COMMITMENT_1.
COMMITMENT_1 = bytes.fromhex(
    "63c7606faee0ee9eff230fec391e64c0c82a0277947973ce7f6f1c9088c821dd"
)
# The public values of fixture session 45818119218's batch-1 and aggregated SNARKs
# (release 0.0.11), as `inspect_proof` printed them.
BATCH_PUBLIC_VALUES_0_0_11 = bytes.fromhex(
    "2ec490cc1239f924eddc97fa13db1e4eee4124cdd85a15a1f518990d54f652d8"
    "63c7606f00000000aee0ee9e00000000ff230fec00000000391e64c0"
    "00000000c82a027700000000947973ce000000007f6f1c900000000088c821dd00000000"
    + "00" * 448
    + "e9414e134bb6945a148a79d44c8e850e5ef582a2b0e8dbfb2428ff0a515a6e66"
)
AGGREGATED_PUBLIC_VALUES_0_0_11 = bytes.fromhex(
    "e7f72bf4c1825a6f852e65fc872c810320218c63ed41cf9bb4345737a4ac31ec"
    "b1449f7500000000ebc58ce2000000000243dd370000000074c8f1a6"
    "0000000017b4e06d00000000f533c7e7000000004c2e8ced00000000c7bfb0b000000000"
    + "00" * 448
    + "e9414e134bb6945a148a79d44c8e850e5ef582a2b0e8dbfb2428ff0a515a6e66"
)
FIXTURE_RUN_URL = (
    "https://github.com/matter-labs/zksync-os-zisk/actions/runs/45818119218"
)

# tools/verifier-gen/data/ZiSK_vk.json after era-contracts#2622.
ZISK_VK_JSON_0_0_9 = """{
  "inner_program_vk": [
    714881513312428246,
    11200215126080624763,
    11939131993649565560,
    10962830692081528578
  ],
  "aggregator_program_vk": [
    11398233775072133853,
    13094553619751218601,
    6186948425065186109,
    5028688609663671816
  ],
  "root_cv_adcop_final": [
    7720455071669353216,
    17350242473202279177,
    12167441154672932695,
    6676005994927600229
  ]
}
"""

# The shape of zksync-os-zisk's guest/GUEST_PROGRAM_VK at 0.0.9, shortened.
INNER_RECORD = """# programVK (ZiSK ROM merkle root) of the reproducible guest ELF.
# Serialization: the four ROM root-hash u64 limbs, big-endian, in order.
#
# Root hash limbs: [714881513312428246, 11200215126080624763,
#                   11939131993649565560, 10962830692081528578]
0x09ebc4f2e17b20d69b6f2850ddfe187ba5b0514abffbcf789823cc6e4e44ef02

# History: 0xc883941cb5a09ec164c357a6cc467104f8b37a0b273e76c8e3ee555f2ecf1a9f
# hash limbs [10598990543184930100, 15397108838842573604,
# 14333166011794942839, 537868756703954105]).
"""

# The constants block of MultiProofRangeVectorTest before the 0.0.9 rotation.
RANGE_VECTOR_BEFORE = """contract MultiProofRangeVectorTest is Test {
    bytes32 internal constant INNER_PROGRAM_VK = 0x93172dbe40432534d5ad84e95b3d2324c6e9a28c1fbd6b770776e4c8d31ec4b9;
    bytes32 internal constant AGGREGATOR_PROGRAM_VK =
        0x558aff7c3342a2636ea8aa9f91d151d0300b7aeccd43d682ac854a79548cb1a7;
    bytes32 internal constant ROOT_C_VADCOP_FINAL = 0xc3f12b9f8707c6a1e96df2bf6702c2ebdfbafedabeac654644a380befe091ac4;
    bytes32 internal constant COMMITMENT_1 = 0x63c7606faee0ee9eff230fec391e64c0c82a0277947973ce7f6f1c9088c821dd;
    bytes32 internal constant CHAINED_PI = 0x00000000108311cf154dafcd8fbeb3d29ff924941d60db59f523d33baa5d2ca5;
    bytes32 internal constant DIGEST = 0x3d7f030cacc8d291c78c7550d5f0c7cbc5fa54095165dda50f899d8eb3809bcd;
}
"""
RANGE_VECTOR_AFTER = """contract MultiProofRangeVectorTest is Test {
    bytes32 internal constant INNER_PROGRAM_VK = 0x09ebc4f2e17b20d69b6f2850ddfe187ba5b0514abffbcf789823cc6e4e44ef02;
    bytes32 internal constant AGGREGATOR_PROGRAM_VK =
        0x9e2ea93a5e08c2ddb5b93323a593ada955dc74d00fac433d45c97da5b90e5e08;
    bytes32 internal constant ROOT_C_VADCOP_FINAL = 0x6b2490fba336fb00f0c8719a961de309a8db6f43a9bfb7575ca5f014d609b265;
    bytes32 internal constant COMMITMENT_1 = 0x63c7606faee0ee9eff230fec391e64c0c82a0277947973ce7f6f1c9088c821dd;
    bytes32 internal constant CHAINED_PI = 0x00000000108311cf154dafcd8fbeb3d29ff924941d60db59f523d33baa5d2ca5;
    bytes32 internal constant DIGEST = 0xec1bf9cd08fde7ce771e6ceb96064b0fa73b3a475e7f6da8675989c3c40b13a2;
}
"""


def real_proof_source(batch, aggregated, run_url, release="0.0.10"):
    """The parts of ZiskVerifierRealProofTest the rotation reads and writes."""
    return f"""/// @dev Fixtures: the zksync-os-zisk ZiSK 1.4.0-alpha pins, guest ELFs
///      reproduced from the release {release} pins in GPU run
///      {run_url}.
contract ZiskVerifierRealProofTest is Test {{
    bytes internal constant BATCH_PROOF =
        hex"{batch.proof.hex()}";

    bytes internal constant BATCH_PUBLIC_VALUES =
        hex"{batch.public_values.hex()}";

    bytes internal constant AGGREGATED_PROOF =
        hex"{aggregated.proof.hex()}";

    bytes internal constant AGGREGATED_PUBLIC_VALUES =
        hex"{aggregated.public_values.hex()}";

    bytes32 internal constant COMMITMENT_1 = 0x{COMMITMENT_1.hex()};
}}
"""


def snark(seed, public_values):
    return SnarkFixture(bytes([seed]) * 2208, public_values)


# Stand-ins for the 0.0.10 fixtures the test held before the 0.0.11 rotation.
OLD_BATCH = snark(
    1,
    expected_public_values(
        PINS_0_0_10.inner_program_vk, COMMITMENT_1, PINS_0_0_10.root_cv_adcop_final
    ),
)
OLD_AGGREGATED = snark(
    2,
    expected_public_values(
        PINS_0_0_10.aggregator_program_vk,
        DIGEST_0_0_10,
        PINS_0_0_10.root_cv_adcop_final,
    ),
)
NEW_SESSION = FixtureSession(
    FIXTURE_RUN_URL,
    snark(3, BATCH_PUBLIC_VALUES_0_0_11),
    snark(4, AGGREGATED_PUBLIC_VALUES_0_0_11),
)
OLD_RUN_URL = "https://github.com/matter-labs/zksync-os-zisk/actions/runs/41671348755"


def inspect_text(fixture):
    """`inspect_proof` output for a fixture, shortened to the lines the script reads."""
    return (
        f"proof bytes: {len(fixture.proof)}\n"
        f"program_vk   = 0x{fixture.public_values[:32].hex()}\n"
        f"PROOF({len(fixture.proof)}): {fixture.proof.hex()}\n"
        f"PUBLIC_VALUES({len(fixture.public_values)}): {fixture.public_values.hex()}\n"
    )


GUEST_MANIFEST = """[package]
name = "zksync-os-zisk-guest"

[dependencies.ziskos]
version = "=1.3.2-alpha"
features = ["zisk-embedded-dlmalloc-alloc"]
"""
AGGREGATOR_MANIFEST = """[package]
name = "zksync-os-zisk-guest-aggregator"

[target.'cfg(all(target_os = "zkvm", target_vendor = "zisk"))'.dependencies]
ziskos = "=1.3.2-alpha"

[target.'cfg(not(all(target_os = "zkvm", target_vendor = "zisk")))'.dependencies]
tiny-keccak = { version = "2.0", features = ["keccak"] }
"""


def record(value: bytes) -> str:
    return f"# Root hash limbs: {to_limbs(value)}\n0x{value.hex()}\n"


def isolated_git_env():
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }


def git_commit_all(repo):
    env = {**os.environ, **isolated_git_env()}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class KeccakTests(unittest.TestCase):
    def test_known_vectors(self):
        for data, digest in [
            (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
            (
                b"abc",
                "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
            ),
            # Longer than one 136-byte block; from `cast keccak`.
            (
                b"a" * 200,
                "96ea54061def936c4be90b518992fdc6f12f535068a256229aca54267b4d084d",
            ),
        ]:
            with self.subTest(length=len(data)):
                self.assertEqual(keccak256(data).hex(), digest)


class PinEncodingTests(unittest.TestCase):
    def test_limbs_match_the_committed_0_0_9_file(self):
        committed = json.loads(ZISK_VK_JSON_0_0_9)
        for name, value in PINS_0_0_9.items():
            with self.subTest(pin=name):
                self.assertEqual(to_limbs(value), committed[name])
                self.assertEqual(from_limbs(committed[name]), value)

    def test_rejects_malformed_limbs(self):
        for limbs in ([1, 2, 3], [1, 2, 3, 1 << 64], [1, 2, 3, -1], [1, 2, 3, True]):
            with self.subTest(limbs=limbs), self.assertRaises(ValueError):
                from_limbs(limbs)
        with self.assertRaises(ValueError):
            to_limbs(bytes(31))

    def test_parses_a_release_record(self):
        self.assertEqual(
            parse_pin_record(INNER_RECORD, "inner"), PINS_0_0_9.inner_program_vk
        )

    def test_a_record_without_limbs_is_accepted(self):
        text = "# comment\n\n0x" + PINS_0_0_9.root_cv_adcop_final.hex() + "\n"
        self.assertEqual(parse_pin_record(text, "root"), PINS_0_0_9.root_cv_adcop_final)

    def test_rejects_a_value_that_contradicts_its_limbs(self):
        # The 0.0.9 limbs above the 0.0.8 value.
        text = INNER_RECORD.replace(
            PINS_0_0_9.inner_program_vk.hex(), PINS_0_0_8.inner_program_vk.hex()
        )
        with self.assertRaisesRegex(ValueError, "recorded limbs"):
            parse_pin_record(text, "inner")

    def test_rejects_malformed_records(self):
        for text, error in [
            ("# only comments\n", "no value"),
            ("# header\n0x1234\n", "32-byte"),
            ("# header\n" + PINS_0_0_9.inner_program_vk.hex() + "\n", "32-byte"),
        ]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                parse_pin_record(text, "pin")

    def test_writes_the_0_0_9_file_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZiSK_vk.json"
            path.write_text(
                json.dumps(
                    {name: to_limbs(value) for name, value in PINS_0_0_8.items()},
                    indent=2,
                )
                + "\n"
            )
            previous = write_zisk_vk_json(path, PINS_0_0_9)
            self.assertEqual(path.read_text(), ZISK_VK_JSON_0_0_9)
        self.assertEqual(previous, PINS_0_0_8)

    def test_refuses_a_file_with_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZiSK_vk.json"
            path.write_text('{"inner_program_vk": [1, 2, 3, 4]}\n')
            with self.assertRaisesRegex(ValueError, "expected the keys"):
                write_zisk_vk_json(path, PINS_0_0_9)
            self.assertEqual(path.read_text(), '{"inner_program_vk": [1, 2, 3, 4]}\n')


class DigestTests(unittest.TestCase):
    def test_0_0_9_vk_hash(self):
        self.assertEqual(PINS_0_0_9.vk_hash(), VK_HASH_0_0_9)

    def test_0_0_9_binding_digest(self):
        self.assertEqual(PINS_0_0_9.binding_digest(CHAINED_PI), DIGEST_0_0_9)

    def test_0_0_8_binding_digest_matches_era_contracts_2583(self):
        self.assertEqual(
            PINS_0_0_8.binding_digest(CHAINED_PI).hex(),
            "3d7f030cacc8d291c78c7550d5f0c7cbc5fa54095165dda50f899d8eb3809bcd",
        )

    def test_0_0_10_vectors_match_era_contracts_2657(self):
        self.assertEqual(PINS_0_0_10.vk_hash(), VK_HASH_0_0_10)
        self.assertEqual(PINS_0_0_10.binding_digest(CHAINED_PI), DIGEST_0_0_10)

    def test_0_0_11_vectors_match_era_contracts_2709(self):
        self.assertEqual(PINS_0_0_11.vk_hash(), VK_HASH_0_0_11)
        self.assertEqual(PINS_0_0_11.binding_digest(CHAINED_PI), DIGEST_0_0_11)

    def test_updates_the_range_vector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MultiProofRangeVectorTest.t.sol"
            path.write_text(RANGE_VECTOR_BEFORE)
            self.assertEqual(update_range_vector_test(path, PINS_0_0_9), DIGEST_0_0_9)
            self.assertEqual(path.read_text(), RANGE_VECTOR_AFTER)
            # A second rotation to the same release changes nothing.
            update_range_vector_test(path, PINS_0_0_9)
            self.assertEqual(path.read_text(), RANGE_VECTOR_AFTER)

    def test_refuses_a_range_vector_without_a_constant(self):
        for name in ("CHAINED_PI", "DIGEST", "AGGREGATOR_PROGRAM_VK"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "MultiProofRangeVectorTest.t.sol"
                text = RANGE_VECTOR_BEFORE.replace(f" {name} ", f" {name}_RENAMED ")
                path.write_text(text)
                with self.assertRaisesRegex(ValueError, name):
                    update_range_vector_test(path, PINS_0_0_9)
                self.assertEqual(path.read_text(), text)


class FixtureTests(unittest.TestCase):
    def test_public_values_match_a_real_0_0_11_session(self):
        self.assertEqual(
            expected_public_values(
                PINS_0_0_11.inner_program_vk,
                COMMITMENT_1,
                PINS_0_0_11.root_cv_adcop_final,
            ),
            BATCH_PUBLIC_VALUES_0_0_11,
        )
        self.assertEqual(
            expected_public_values(
                PINS_0_0_11.aggregator_program_vk,
                DIGEST_0_0_11,
                PINS_0_0_11.root_cv_adcop_final,
            ),
            AGGREGATED_PUBLIC_VALUES_0_0_11,
        )

    def test_parses_inspect_output(self):
        self.assertEqual(
            parse_inspect(inspect_text(NEW_SESSION.batch), "batch"), NEW_SESSION.batch
        )

    def test_rejects_malformed_inspect_output(self):
        text = inspect_text(NEW_SESSION.batch)
        proof = NEW_SESSION.batch.proof.hex()
        for broken in [
            text.replace("PROOF(2208)", "PROOF(2207)"),
            text.replace(proof, proof[:-2]).replace("PROOF(2208)", "PROOF(2207)"),
            text + text.splitlines()[-1] + "\n",
            "\n".join(
                line for line in text.splitlines() if "PUBLIC_VALUES" not in line
            ),
        ]:
            with self.subTest(broken=broken[:80]), self.assertRaises(ValueError):
                parse_inspect(broken, "batch")

    def test_reads_the_real_proof_commitment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZiskVerifierRealProofTest.t.sol"
            path.write_text(real_proof_source(OLD_BATCH, OLD_AGGREGATED, OLD_RUN_URL))
            self.assertEqual(real_proof_commitment(path), COMMITMENT_1)
            path.write_text(path.read_text().replace("COMMITMENT_1", "COMMITMENT_ONE"))
            with self.assertRaisesRegex(ValueError, "COMMITMENT_1"):
                real_proof_commitment(path)

    def test_updates_the_real_proof_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZiskVerifierRealProofTest.t.sol"
            path.write_text(real_proof_source(OLD_BATCH, OLD_AGGREGATED, OLD_RUN_URL))
            expected = real_proof_source(
                NEW_SESSION.batch, NEW_SESSION.aggregated, FIXTURE_RUN_URL, "0.0.11"
            )
            update_real_proof_test(
                path, NEW_SESSION, "matter-labs/zksync-os-zisk", "0.0.11"
            )
            self.assertEqual(path.read_text(), expected)
            # A second rotation to the same release changes nothing.
            update_real_proof_test(
                path, NEW_SESSION, "matter-labs/zksync-os-zisk", "0.0.11"
            )
            self.assertEqual(path.read_text(), expected)

    def test_the_release_in_the_notice_is_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ZiskVerifierRealProofTest.t.sol"
            text = real_proof_source(OLD_BATCH, OLD_AGGREGATED, OLD_RUN_URL).replace(
                "the release 0.0.10 pins", "the pins"
            )
            path.write_text(text)
            update_real_proof_test(
                path, NEW_SESSION, "matter-labs/zksync-os-zisk", "0.0.11"
            )
            self.assertIn(
                f"the pins in GPU run\n///      {FIXTURE_RUN_URL}.", path.read_text()
            )

    def test_refuses_a_real_proof_test_without_a_constant_or_run(self):
        source = real_proof_source(OLD_BATCH, OLD_AGGREGATED, OLD_RUN_URL)
        for old, new, message in [
            (" AGGREGATED_PROOF ", " AGGREGATED_SNARK ", "AGGREGATED_PROOF"),
            (OLD_RUN_URL, "https://example.com/run", "run URL"),
        ]:
            with self.subTest(old=old), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "ZiskVerifierRealProofTest.t.sol"
                text = source.replace(old, new)
                path.write_text(text)
                with self.assertRaisesRegex(ValueError, message):
                    update_real_proof_test(
                        path, NEW_SESSION, "matter-labs/zksync-os-zisk", "0.0.11"
                    )
                self.assertEqual(path.read_text(), text)


class FindFixtureSessionTests(unittest.TestCase):
    REPO = "matter-labs/zksync-os-zisk"

    def find(self, runs):
        """Runs `find_fixture_session` over `runs`: (id, expired, files) newest first."""
        by_id = {run_id: (expired, files) for run_id, expired, files in runs}
        downloads = []

        def api(endpoint, accept=None):
            prefix = f"repos/{self.REPO}/actions/"
            self.assertTrue(endpoint.startswith(prefix), endpoint)
            if endpoint.startswith(f"{prefix}workflows/fixture-session.yaml/runs?"):
                self.assertIn("status=success", endpoint)
                return json.dumps(
                    {
                        "workflow_runs": [
                            {"id": run_id, "html_url": f"https://run/{run_id}"}
                            for run_id, _, _ in runs
                        ]
                    }
                )
            run_id = int(endpoint.removeprefix(f"{prefix}runs/").split("/")[0])
            self.assertTrue(endpoint.endswith("/artifacts?name=zisk-fixture-session"))
            expired, _ = by_id[run_id]
            return json.dumps(
                {"artifacts": [{"expired": expired}] if expired is not None else []}
            )

        def download(repository, run_id):
            self.assertEqual(repository, self.REPO)
            downloads.append(run_id)
            return by_id[run_id][1]

        session = find_fixture_session(
            self.REPO,
            PINS_0_0_11,
            COMMITMENT_1,
            DIGEST_0_0_11,
            logging.getLogger("test"),
            api,
            download,
        )
        return session, downloads

    @staticmethod
    def files(session):
        return {
            BATCH_INSPECT: inspect_text(session.batch),
            AGGREGATED_INSPECT: inspect_text(session.aggregated),
        }

    def test_takes_the_newest_session_that_proved_the_pins(self):
        older = FixtureSession(
            "https://run/1",
            snark(9, NEW_SESSION.batch.public_values),
            snark(9, NEW_SESSION.aggregated.public_values),
        )
        stale = FixtureSession("https://run/5", OLD_BATCH, OLD_AGGREGATED)
        session, downloads = self.find(
            [
                (7, True, None),  # artifact expired
                (6, None, None),  # no artifact
                (5, False, self.files(stale)),  # the previous release
                (4, False, {BATCH_INSPECT: "garbage"}),  # unreadable
                (3, False, self.files(NEW_SESSION)),
                (1, False, self.files(older)),
            ]
        )
        self.assertEqual(
            session,
            FixtureSession("https://run/3", NEW_SESSION.batch, NEW_SESSION.aggregated),
        )
        self.assertEqual(downloads, [5, 4, 3])

    def test_a_session_must_match_both_proofs(self):
        mixed = FixtureSession("https://run/2", NEW_SESSION.batch, OLD_AGGREGATED)
        session, _ = self.find([(2, False, self.files(mixed))])
        self.assertIsNone(session)

    def test_another_first_batch_does_not_match(self):
        batch = snark(
            3,
            expected_public_values(
                PINS_0_0_11.inner_program_vk,
                b"\x01" * 32,
                PINS_0_0_11.root_cv_adcop_final,
            ),
        )
        session, _ = self.find(
            [(2, False, self.files(FixtureSession("", batch, NEW_SESSION.aggregated)))]
        )
        self.assertIsNone(session)

    def test_no_session_left(self):
        self.assertEqual(self.find([]), (None, []))


class SnarkVerifierTests(unittest.TestCase):
    def test_the_upstream_file_passes_and_returns_its_sha256(self):
        self.assertEqual(
            check_snark_verifier(PILFFLONK_VERIFIER, PILFFLONK_VERIFIER, "1.4.0-alpha"),
            hashlib.sha256(PILFFLONK_VERIFIER.encode()).hexdigest(),
        )

    def test_any_difference_fails_with_the_redeploy_message(self):
        for upstream in [
            PILFFLONK_VERIFIER.replace("C0x = 1", "C0x = 2"),
            PILFFLONK_VERIFIER.replace("MIT OR Apache-2.0", "MIT"),
            PILFFLONK_VERIFIER + "\n",
        ]:
            with (
                self.subTest(upstream=upstream),
                self.assertRaises(SnarkVerifierMismatch) as error,
            ):
                check_snark_verifier(PILFFLONK_VERIFIER, upstream, "9.9.9")
            message = str(error.exception)
            self.assertIn("v9.9.9", message)
            self.assertIn("zisk-contracts/poseidon1/PilfflonkVerifier.sol", message)
            self.assertIn("deploy it by hand", message)


class ZiskVersionTests(unittest.TestCase):
    def test_reads_both_dependency_forms(self):
        self.assertEqual(
            zisk_version({"guest": GUEST_MANIFEST, "aggregator": AGGREGATOR_MANIFEST}),
            "1.3.2-alpha",
        )

    def test_refuses_guests_on_different_releases(self):
        with self.assertRaisesRegex(ValueError, "different ZiSK releases"):
            zisk_version(
                {
                    "guest": GUEST_MANIFEST,
                    "aggregator": AGGREGATOR_MANIFEST.replace("1.3.2", "1.3.3"),
                }
            )

    def test_refuses_an_inexact_or_missing_requirement(self):
        for manifest in [
            GUEST_MANIFEST.replace('"=1.3.2-alpha"', '"1.3.2-alpha"'),
            GUEST_MANIFEST.replace('version = "=1.3.2-alpha"', 'git = "https://x"'),
            "[package]\nname = 'guest'\n",
        ]:
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                zisk_version({"guest": manifest})


class ReadReleaseTests(unittest.TestCase):
    REPO = "matter-labs/zksync-os-zisk"
    COMMIT = "999da9a7777872cbe8a0222db9389ac70bb84c88"

    def api(self, missing_release=False):
        files = {
            "guest/GUEST_PROGRAM_VK": INNER_RECORD,
            "guest-aggregator/GUEST_PROGRAM_VK": record(
                PINS_0_0_9.aggregator_program_vk
            ),
            "guest-aggregator/ROOT_C_VADCOP_FINAL": record(
                PINS_0_0_9.root_cv_adcop_final
            ),
            "guest/Cargo.toml": GUEST_MANIFEST,
            "guest-aggregator/Cargo.toml": AGGREGATOR_MANIFEST,
        }
        calls = []

        def api(endpoint, accept=None):
            calls.append((endpoint, accept))
            if endpoint == f"repos/{self.REPO}/releases/tags/0.0.9":
                if missing_release:
                    raise RuntimeError("`gh api` failed: Not Found (HTTP 404)")
                return json.dumps({"html_url": "https://release"})
            if endpoint == f"repos/{self.REPO}/commits/0.0.9":
                self.assertEqual(accept, "application/vnd.github.sha")
                return self.COMMIT + "\n"
            prefix = f"repos/{self.REPO}/contents/"
            path, ref = endpoint.removeprefix(prefix).split("?ref=")
            # Every file is read at the tag's commit, not at the movable tag.
            self.assertEqual(ref, self.COMMIT)
            self.assertEqual(accept, "application/vnd.github.raw")
            return files[path]

        return api, calls

    def test_reads_the_pins_and_version_at_the_tag_commit(self):
        api, calls = self.api()
        release = read_release(self.REPO, "0.0.9", api)
        self.assertEqual(
            release,
            ZiskRelease(
                self.REPO,
                "0.0.9",
                self.COMMIT,
                "https://release",
                "1.3.2-alpha",
                PINS_0_0_9,
            ),
        )
        self.assertEqual(len(calls), 7)

    def test_an_unpublished_tag_fails(self):
        api, calls = self.api(missing_release=True)
        with self.assertRaisesRegex(RuntimeError, "not a published release"):
            read_release(self.REPO, "0.0.9", api)
        self.assertEqual(len(calls), 1)


class ScriptTests(unittest.TestCase):
    """Runs the script on a scratch contracts checkout.

    The GitHub reads, the verifier generator and recompute_hashes.sh are mocked:
    the test covers what the script writes and checks around them.
    """

    COMMIT = "999da9a7777872cbe8a0222db9389ac70bb84c88"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "era-contracts"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for path, text in {
            ZISK_VK_JSON: json.dumps(
                {name: to_limbs(value) for name, value in PINS_0_0_8.items()}, indent=2
            )
            + "\n",
            SNARK_VERIFIER: PILFFLONK_VERIFIER,
            ZISK_VERIFIER: self.verifier(PINS_0_0_8),
            RANGE_VECTOR_TEST: RANGE_VECTOR_BEFORE,
            REAL_PROOF_TEST: real_proof_source(OLD_BATCH, OLD_AGGREGATED, OLD_RUN_URL),
            Path("AllContractsHashes.json"): "[]\n",
            Path("recompute_hashes.sh"): "",
        }.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        git_commit_all(self.repo)
        self.release = ZiskRelease(
            "matter-labs/zksync-os-zisk",
            "0.0.9",
            self.COMMIT,
            "https://github.com/matter-labs/zksync-os-zisk/releases/tag/0.0.9",
            "1.4.0-alpha",
            PINS_0_0_9,
        )
        self.upstream = PILFFLONK_VERIFIER
        self.commands = []

    @staticmethod
    def verifier(pins):
        return f"bytes32 private constant _VK_HASH = 0x{pins.vk_hash().hex()};\n"

    # The session the fixture search returns; the search has its own tests.
    SESSION = FixtureSession(
        FIXTURE_RUN_URL,
        snark(
            3,
            expected_public_values(
                PINS_0_0_9.inner_program_vk,
                COMMITMENT_1,
                PINS_0_0_9.root_cv_adcop_final,
            ),
        ),
        snark(
            4,
            expected_public_values(
                PINS_0_0_9.aggregator_program_vk,
                DIGEST_0_0_9,
                PINS_0_0_9.root_cv_adcop_final,
            ),
        ),
    )

    def run_script(self, generated_pins=PINS_0_0_9, recompute=None, session=SESSION):
        def sh(command, **kwargs):
            self.commands.append((command, kwargs))
            if command[0] == "cargo":
                # Stands in for the generator, which writes the VK hash of the JSON.
                written = json.loads((self.repo / ZISK_VK_JSON).read_text())
                self.assertEqual(
                    {name: from_limbs(limbs) for name, limbs in written.items()},
                    dict(PINS_0_0_9.items()),
                )
                (self.repo / ZISK_VERIFIER).write_text(self.verifier(generated_pins))
            else:
                (self.repo / "AllContractsHashes.json").write_text('["rotated"]\n')
                if recompute:
                    recompute(self.repo)

        ctx = MagicMock(
            workspace=self.workspace,
            repo_dir=self.repo,
            logger=logging.getLogger("test"),
        )
        ctx.sh.side_effect = sh
        with (
            patch("scripts.update_zisk_vk.utils.require_cmds"),
            patch("scripts.update_zisk_vk.read_release", return_value=self.release),
            patch("scripts.update_zisk_vk.gh_api", return_value=self.upstream) as api,
            patch(
                "scripts.update_zisk_vk.find_fixture_session", return_value=session
            ) as self.find,
            patch.dict(
                os.environ,
                {
                    "ZISK_RELEASE_TAG": "0.0.9",
                    "UPSTREAM_FOUNDRY_DIR": "/opt/foundry",
                    "PATH": "/usr/bin:/bin",
                    "RUN_URL": "https://run",
                },
                clear=True,
            ),
        ):
            script(ctx)
        return api

    def test_rotates_the_checkout_and_writes_the_pr_body(self):
        api = self.run_script()
        api.assert_called_once_with(
            "repos/0xPolygonHermez/zisk/contents/"
            "zisk-contracts/poseidon1/PilfflonkVerifier.sol?ref=v1.4.0-alpha",
            "application/vnd.github.raw",
        )
        self.assertEqual((self.repo / ZISK_VK_JSON).read_text(), ZISK_VK_JSON_0_0_9)
        self.assertEqual(
            (self.repo / RANGE_VECTOR_TEST).read_text(), RANGE_VECTOR_AFTER
        )

        (cargo, cargo_kwargs), (recompute, recompute_kwargs) = self.commands
        self.assertEqual(cargo_kwargs["cwd"], self.repo / "tools/verifier-gen")
        self.assertEqual(
            cargo[cargo.index("--") + 1 :],
            [
                "--variant",
                "zisk",
                "--zisk_vk_path",
                "data/ZiSK_vk.json",
                "--zisk_output_path",
                "../../l1-contracts/contracts/state-transition/verifiers/ZiskVerifier.sol",
            ],
        )
        self.assertEqual(recompute, ["bash", str(self.repo / "recompute_hashes.sh")])
        self.assertEqual(
            recompute_kwargs["env"], {"PATH": f"/opt/foundry{os.pathsep}/usr/bin:/bin"}
        )

        manifest = json.loads((self.workspace / "zisk-rotation.json").read_text())
        self.assertEqual(manifest["zisk_release_commit"], self.COMMIT)
        self.assertEqual(manifest["vk_hash"], f"0x{VK_HASH_0_0_9.hex()}")
        self.assertEqual(
            manifest["previous_vk_hash"], f"0x{PINS_0_0_8.vk_hash().hex()}"
        )
        self.assertEqual(manifest["binding_digest"], f"0x{DIGEST_0_0_9.hex()}")
        self.assertEqual(
            manifest["pins"]["root_cv_adcop_final"]["limbs"],
            json.loads(ZISK_VK_JSON_0_0_9)["root_cv_adcop_final"],
        )
        sha256 = hashlib.sha256(PILFFLONK_VERIFIER.encode()).hexdigest()
        self.assertEqual(manifest["snark_verifier_sha256"], sha256)
        self.assertEqual(manifest["fixture_session_run_url"], FIXTURE_RUN_URL)
        body = (self.workspace / "zisk_pr_body.md").read_text()
        self.assertIn(sha256, body)
        self.assertIn(f"[fixture session 45818119218]({FIXTURE_RUN_URL})", body)
        self.assertIn("[zksync-os-scripts run](https://run)", body)

        # The search gets the test's first commitment and the new binding digest.
        repository, pins, commitment, digest, _ = self.find.call_args.args
        self.assertEqual(
            (repository, pins, commitment, digest),
            ("matter-labs/zksync-os-zisk", PINS_0_0_9, COMMITMENT_1, DIGEST_0_0_9),
        )
        self.assertEqual(
            (self.repo / REAL_PROOF_TEST).read_text(),
            real_proof_source(
                self.SESSION.batch, self.SESSION.aggregated, FIXTURE_RUN_URL, "0.0.9"
            ),
        )

    def test_without_a_fixture_session_the_proofs_stay(self):
        before = (self.repo / REAL_PROOF_TEST).read_text()
        self.run_script(session=None)
        self.assertEqual((self.repo / REAL_PROOF_TEST).read_text(), before)
        manifest = json.loads((self.workspace / "zisk-rotation.json").read_text())
        self.assertIsNone(manifest["fixture_session_run_url"])
        body = (self.workspace / "zisk_pr_body.md").read_text()
        self.assertIn("fails until a fixture session for this release", body)
        self.assertIn("fixture-session.yaml", body)

    def test_a_generated_vk_hash_that_disagrees_with_the_pins_fails(self):
        with self.assertRaisesRegex(RuntimeError, "differs from keccak256"):
            self.run_script(generated_pins=PINS_0_0_8)
        self.assertFalse((self.workspace / "zisk_pr_body.md").exists())

    def test_a_change_outside_the_rotated_files_fails(self):
        def touch_lockfile(repo):
            (repo / "yarn.lock").write_text("changed\n")

        with self.assertRaisesRegex(RuntimeError, r"\['yarn.lock'\]"):
            self.run_script(recompute=touch_lockfile)
        self.assertFalse((self.workspace / "zisk_pr_body.md").exists())

    def test_a_snark_verifier_mismatch_stops_before_any_write(self):
        self.upstream = self.upstream.replace("C0x = 1", "C0x = 5")
        with self.assertRaises(SnarkVerifierMismatch):
            self.run_script()
        self.assertEqual(self.commands, [])
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(status, "")


class PrBodyTests(unittest.TestCase):
    def test_lists_pins_and_follow_ups(self):
        release = ZiskRelease(
            "matter-labs/zksync-os-zisk",
            "0.0.9",
            "a" * 40,
            "https://release",
            "1.4.0-alpha",
            PINS_0_0_9,
        )
        body = render_pr_body(Rotation(release, PINS_0_0_8, DIGEST_0_0_9, "ab" * 32))
        self.assertIn("[zksync-os-zisk release 0.0.9](https://release)", body)
        self.assertIn(
            f"| inner program VK (`guest/GUEST_PROGRAM_VK`) | `0x{PINS_0_0_9.inner_program_vk.hex()}` "
            f"| `0x{PINS_0_0_8.inner_program_vk.hex()}` |",
            body,
        )
        self.assertIn(f"`0x{VK_HASH_0_0_9.hex()}`", body)
        self.assertIn(f"`0x{DIGEST_0_0_9.hex()}`", body)
        self.assertIn("ZiskVerifierRealProofTest", body)
        self.assertIn("fails until a fixture session for this release", body)
        self.assertIn("RTX 6000", body)
        self.assertIn("*Update All Generated Artifacts*", body)
        self.assertIn("`IZiskSnarkVerifier`", body)
        self.assertNotIn("PLONK", body)
        self.assertIn("Generated by zksync-os-scripts", body)

    def test_cites_the_fixture_session(self):
        release = ZiskRelease(
            "matter-labs/zksync-os-zisk",
            "0.0.11",
            "a" * 40,
            "https://release",
            "1.4.0-alpha",
            PINS_0_0_11,
        )
        body = render_pr_body(
            Rotation(release, PINS_0_0_10, DIGEST_0_0_11, "ab" * 32, FIXTURE_RUN_URL)
        )
        self.assertIn(
            f"`ZiskVerifierRealProofTest` takes the batch-1 and aggregated pilfflonk "
            f"proofs of [fixture session 45818119218]({FIXTURE_RUN_URL})",
            body,
        )
        not_updated = body.split("**Not updated by the automation:**")[1]
        self.assertNotIn("Real-proof fixtures", not_updated)
        self.assertIn("The prose that names the release", not_updated)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())
        cls.steps = cls.workflow["jobs"]["update-zisk-vk"]["steps"]
        cls.names = [step.get("name") for step in cls.steps]

    def step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    def test_inputs_mirror_update_vk(self):
        # PyYAML reads the `on` key as True.
        triggers = self.workflow[True]
        for trigger in ("workflow_dispatch", "workflow_call"):
            with self.subTest(trigger=trigger):
                self.assertEqual(
                    list(triggers[trigger]["inputs"]),
                    [
                        "zisk_release_tag",
                        "era_contracts_branch",
                        "commit_changes",
                        "open_pr",
                    ],
                )

    def test_installs_upstream_foundry_like_update_vk(self):
        # UpstreamFoundryStepTests in test_update_vk.py exercise that step.
        name = "Install the contracts' pinned upstream Foundry"
        update_vk = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/update-vk.yaml").read_text()
        )
        original = next(
            step
            for step in update_vk["jobs"]["check-vk"]["steps"]
            if step.get("name") == name
        )
        self.assertEqual(self.step(name)["run"], original["run"])

    def test_step_order(self):
        order = [
            "Checkout contracts",
            "Install the contracts' pinned upstream Foundry",
            "Rotate the ZiSK pins",
            "Upload rotation summary",
            "Commit era-contracts changes",
        ]
        self.assertEqual(
            [self.names.index(name) for name in order],
            sorted(self.names.index(name) for name in order),
        )

    def test_reads_releases_with_release_token_or_workflow_token(self):
        env = self.step("Rotate the ZiSK pins")["env"]
        self.assertEqual(
            env["GH_TOKEN"], "${{ secrets.RELEASE_TOKEN || github.token }}"
        )
        self.assertEqual(env["ZISK_RELEASE_TAG"], "${{ inputs.zisk_release_tag }}")


# Stands in for `gh`: logs each call, arguments joined by \x1f, one call per line.
# `pr create` prints the new PR's URL; `workflow run` fails if STUB_DISPATCH_FAILS is set.
STUB_GH = """#!/usr/bin/env bash
printf '%s\\x1f' "$@" >> "${STUB_DIR}/gh.log"
printf '\\n' >> "${STUB_DIR}/gh.log"
if [[ "$1 $2" == "pr create" ]]; then
  echo "https://github.com/matter-labs/era-contracts/pull/2709"
elif [[ "$1 $2" == "workflow run" && -n "${STUB_DISPATCH_FAILS:-}" ]]; then
  echo "HTTP 403: Resource not accessible by integration" >&2
  exit 1
fi
"""


class CommitStepTests(unittest.TestCase):
    STEP = "Commit era-contracts changes"
    TITLE = "chore(verifier): rotate ZiSK pins to zksync-os-zisk release 0.0.9"

    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        cls.step = next(
            step
            for step in workflow["jobs"]["update-zisk-vk"]["steps"]
            if step.get("name") == cls.STEP
        )

    def run_step(self, open_pr, change, dispatch_fails=False):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stub_dir = tmp / "stub"
            stub_dir.mkdir()
            stub_bin = tmp / "bin"
            stub_bin.mkdir()
            (stub_bin / "gh").write_text(STUB_GH)
            (stub_bin / "gh").chmod(0o755)
            # Records its arguments and the message file in place of the real script.
            push = tmp / "push-signed-commit.sh"
            push.write_text(
                "#!/usr/bin/env bash\n"
                'printf \'%s\\x1f\' "$@" > "${STUB_DIR}/push.args"\n'
                'cp "$3" "${STUB_DIR}/push.message"\n'
            )
            body = tmp / "zisk_pr_body.md"
            body.write_text("body\n")
            repo = tmp / "era-contracts"
            repo.mkdir()
            for name in ("ZiSK_vk.json", "ZiskVerifier.sol"):
                (repo / name).write_text(f"{name}\n")
            base = git_commit_all(repo)
            change(repo)
            result = subprocess.run(
                ["bash", "-e", "-c", self.step["run"]],
                check=False,
                cwd=repo,
                env={
                    **isolated_git_env(),
                    "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
                    "STUB_DIR": str(stub_dir),
                    "GH_TOKEN": "token",
                    "TARGET_REPOSITORY": "matter-labs/era-contracts",
                    "PUSH_SIGNED_COMMIT": str(push),
                    "CONTRACTS_BRANCH": "draft/v0.34.0",
                    "OPEN_PR": open_pr,
                    "ZISK_RELEASE_TAG": "0.0.9",
                    "PR_BODY_FILE": str(body),
                    "COMMIT_TITLE": self.TITLE,
                    "ARTIFACTS_WORKFLOW": self.step["env"]["ARTIFACTS_WORKFLOW"],
                    **({"STUB_DISPATCH_FAILS": "1"} if dispatch_fails else {}),
                },
                capture_output=True,
                text=True,
            )
            args_path = stub_dir / "push.args"
            push_args = (
                args_path.read_text().split("\x1f")[:-1] if args_path.exists() else None
            )
            message_path = stub_dir / "push.message"
            message = message_path.read_text() if message_path.exists() else None
            log = stub_dir / "gh.log"
            calls = (
                [line.split("\x1f")[:-1] for line in log.read_text().splitlines()]
                if log.exists()
                else []
            )
            return result, base, push_args, message, calls, str(body)

    @staticmethod
    def rotate(repo):
        (repo / "ZiSK_vk.json").write_text("rotated\n")
        (repo / "ZiskVerifier.sol").write_text("regenerated\n")

    def test_never_commits_or_pushes_with_git(self):
        self.assertNotRegex(self.step["run"], r"git (commit|push)\b")

    def test_skips_when_the_release_is_already_pinned(self):
        result, _, push_args, _, calls, _ = self.run_step("true", lambda repo: None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already pins zksync-os-zisk 0.0.9", result.stdout)
        self.assertIsNone(push_args)
        self.assertEqual(calls, [])

    def test_opens_a_pr_titled_after_the_release(self):
        result, base, push_args, message, calls, body = self.run_step(
            "true", self.rotate
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        branch, parent, _, expected_old, *paths = push_args
        self.assertRegex(branch, r"^update-zisk-vk-0\.0\.9-[0-9]+$")
        self.assertEqual(parent, base)
        self.assertEqual(expected_old, "")
        self.assertEqual(sorted(paths), ["ZiSK_vk.json", "ZiskVerifier.sol"])
        self.assertEqual(message, f"{self.TITLE}\n")
        self.assertEqual(
            calls,
            [
                ["pr", "create", "--repo", "matter-labs/era-contracts"]
                + ["--title", self.TITLE, "--body-file", body]
                + ["--base", "draft/v0.34.0", "--head", branch],
                # Regenerates the chain states from the rotation commit, for the new PR.
                ["workflow", "run", "update-generated-artifacts.yaml"]
                + ["--repo", "matter-labs/era-contracts", "--ref", branch]
                + ["-f", "pr_number=2709"],
            ],
        )
        self.assertNotIn("::warning::", result.stdout)

    def test_a_failed_dispatch_only_warns(self):
        result, _, push_args, _, calls, _ = self.run_step(
            "true", self.rotate, dispatch_fails=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[:2] for call in calls], [["pr", "create"], ["workflow", "run"]]
        )
        self.assertIn(
            "::warning::Could not dispatch update-generated-artifacts.yaml for "
            "https://github.com/matter-labs/era-contracts/pull/2709; dispatch it from "
            f"{push_args[0]} to regenerate the chain states.",
            result.stdout,
        )

    def test_pushes_onto_the_contracts_branch_without_a_pr(self):
        result, base, push_args, _, calls, _ = self.run_step("false", self.rotate)
        self.assertEqual(result.returncode, 0, result.stderr)
        branch, parent, _, expected_old, *_ = push_args
        self.assertEqual(branch, "draft/v0.34.0")
        self.assertEqual(parent, base)
        # Moving the branch requires it to still be at the commit the rotation ran on.
        self.assertEqual(expected_old, base)
        # The chain-state workflow needs a PR, so nothing is dispatched.
        self.assertEqual(calls, [])
        self.assertIn("::notice::", result.stdout)
        self.assertIn("chain states were not regenerated", result.stdout)


if __name__ == "__main__":
    unittest.main()
