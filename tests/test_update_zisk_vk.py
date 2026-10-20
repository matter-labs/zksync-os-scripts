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
    PLONK_KEY_JSON,
    RANGE_VECTOR_TEST,
    ZISK_VERIFIER,
    ZISK_VK_JSON,
    PlonkKeyMismatch,
    Rotation,
    ZiskPins,
    ZiskRelease,
    check_plonk_key,
    committed_plonk_constants,
    from_limbs,
    parse_pin_record,
    plonk_key_mismatches,
    read_release,
    render_pr_body,
    script,
    to_limbs,
    update_range_vector_test,
    write_zisk_vk_json,
    zisk_version,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data" / "zisk"
WORKFLOW = REPO_ROOT / ".github/workflows/update-zisk-vk.yaml"

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


class PlonkKeyTests(unittest.TestCase):
    def setUp(self):
        self.key = json.loads((DATA / "ZiSK_plonk_verification_key.json").read_text())
        self.upstream = (DATA / "PlonkVerifier-v1.3.2-alpha-constants.sol").read_text()

    def test_committed_key_matches_upstream_1_3_2_alpha(self):
        self.assertEqual(len(committed_plonk_constants(self.key)), 25)
        self.assertEqual(plonk_key_mismatches(self.key, self.upstream), [])
        self.assertEqual(check_plonk_key(self.key, self.upstream, "1.3.2-alpha"), 25)

    def test_maps_x2_coordinates_like_snarkjs(self):
        constants = committed_plonk_constants(self.key)
        self.assertEqual(constants["X2x1"], int(self.key["X_2"][0][0]))
        self.assertEqual(constants["X2y2"], int(self.key["X_2"][1][1]))
        self.assertEqual(constants["n"], 16777216)

    def test_a_changed_circuit_fails_with_the_backend_message(self):
        changed = self.upstream.replace(
            "S2y  = 8754849180022946228560993117747864340770788085114271873100323038997750357449",
            "S2y  = 1",
        )
        self.assertEqual(
            plonk_key_mismatches(self.key, changed),
            [
                "S2y: committed 8754849180022946228560993117747864340770788085114271873100323038997750357449, upstream 1"
            ],
        )
        with self.assertRaises(PlonkKeyMismatch) as error:
            check_plonk_key(self.key, changed, "9.9.9")
        message = str(error.exception)
        self.assertIn("v9.9.9", message)
        self.assertIn("S2y", message)
        self.assertIn("zisk_plonk_verifier_addr", message)

    def test_a_domain_change_and_a_missing_constant_are_mismatches(self):
        changed = self.upstream.replace("= 16777216;", "= 33554432;").replace(
            "uint256 constant k2   = 3;", ""
        )
        self.assertEqual(
            plonk_key_mismatches(self.key, changed),
            [
                "n: committed 16777216, upstream 33554432",
                "k2: committed 3, missing upstream",
            ],
        )

    def test_rejects_a_projective_point(self):
        self.key["Qm"][2] = "2"
        with self.assertRaisesRegex(ValueError, "Qm is not in affine form"):
            committed_plonk_constants(self.key)


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
            PLONK_KEY_JSON: (DATA / "ZiSK_plonk_verification_key.json").read_text(),
            ZISK_VERIFIER: self.verifier(PINS_0_0_8),
            RANGE_VECTOR_TEST: RANGE_VECTOR_BEFORE,
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
            "1.3.2-alpha",
            PINS_0_0_9,
        )
        self.upstream = (DATA / "PlonkVerifier-v1.3.2-alpha-constants.sol").read_text()
        self.commands = []

    @staticmethod
    def verifier(pins):
        return f"bytes32 private constant _VK_HASH = 0x{pins.vk_hash().hex()};\n"

    def run_script(self, generated_pins=PINS_0_0_9, recompute=None):
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
            "repos/0xPolygonHermez/zisk/contents/zisk-contracts/PlonkVerifier.sol"
            "?ref=v1.3.2-alpha",
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
        self.assertEqual(manifest["plonk_key_fields_matching_upstream"], 25)
        body = (self.workspace / "zisk_pr_body.md").read_text()
        self.assertIn("fixture-session.yaml", body)
        self.assertIn("RTX 6000", body)
        self.assertIn("[zksync-os-scripts run](https://run)", body)

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

    def test_a_plonk_key_mismatch_stops_before_any_write(self):
        self.upstream = self.upstream.replace(
            "constant k1   = 2;", "constant k1   = 5;"
        )
        with self.assertRaises(PlonkKeyMismatch):
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
            "1.3.2-alpha",
            PINS_0_0_9,
        )
        body = render_pr_body(Rotation(release, PINS_0_0_8, DIGEST_0_0_9, 25))
        self.assertIn("[zksync-os-zisk release 0.0.9](https://release)", body)
        self.assertIn(
            f"| inner program VK (`guest/GUEST_PROGRAM_VK`) | `0x{PINS_0_0_9.inner_program_vk.hex()}` "
            f"| `0x{PINS_0_0_8.inner_program_vk.hex()}` |",
            body,
        )
        self.assertIn(f"`0x{VK_HASH_0_0_9.hex()}`", body)
        self.assertIn(f"`0x{DIGEST_0_0_9.hex()}`", body)
        self.assertIn("ZiskVerifierRealProofTest", body)
        self.assertIn("Generated by zksync-os-scripts", body)


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
STUB_GH = """#!/usr/bin/env bash
printf '%s\\x1f' "$@" >> "${STUB_DIR}/gh.log"
printf '\\n' >> "${STUB_DIR}/gh.log"
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

    def run_step(self, open_pr, change):
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
                + ["--base", "draft/v0.34.0", "--head", branch]
            ],
        )

    def test_pushes_onto_the_contracts_branch_without_a_pr(self):
        result, base, push_args, _, calls, _ = self.run_step("false", self.rotate)
        self.assertEqual(result.returncode, 0, result.stderr)
        branch, parent, _, expected_old, *_ = push_args
        self.assertEqual(branch, "draft/v0.34.0")
        self.assertEqual(parent, base)
        # Moving the branch requires it to still be at the commit the rotation ran on.
        self.assertEqual(expected_old, base)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
