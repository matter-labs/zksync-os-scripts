import io
import logging
import os
import resource
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import yaml

from scripts.update_vk import (
    WRAPPER_STACK_BYTES,
    download_os_binary,
    prepare_wrapper_stack,
    script,
)


class WrapperStackTests(unittest.TestCase):
    @patch("scripts.update_vk.resource.setrlimit")
    @patch("scripts.update_vk.resource.getrlimit")
    def test_main_thread_stack_limit(self, getrlimit, setrlimit):
        for soft in [8 * 1024 * 1024, WRAPPER_STACK_BYTES, resource.RLIM_INFINITY]:
            with self.subTest(soft=soft):
                getrlimit.return_value = (soft, resource.RLIM_INFINITY)
                setrlimit.reset_mock()
                prepare_wrapper_stack()
                if soft == 8 * 1024 * 1024:
                    setrlimit.assert_called_once_with(
                        resource.RLIMIT_STACK,
                        (WRAPPER_STACK_BYTES, resource.RLIM_INFINITY),
                    )
                else:
                    setrlimit.assert_not_called()


class DownloadBinaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.ctx = MagicMock(workspace=self.workspace)
        self.binary = self.workspace / "multiblock_batch.bin"
        self.binary.write_bytes(b"old release")

    def test_private_release_replaces_cached_binary(self):
        def download(command):
            self.assertEqual(command[3], "v0.5.5")
            self.assertEqual(
                command[command.index("--repo") + 1], "matter-labs/zksync-os-private"
            )
            Path(command[command.index("--output") + 1]).write_bytes(b"v0.5.5")

        self.ctx.sh.side_effect = download
        result = download_os_binary(
            self.ctx,
            "v0.5.5",
            "https://github.com/matter-labs/zksync-os",
            "matter-labs/zksync-os-private",
        )
        self.assertEqual(result.read_bytes(), b"v0.5.5")
        self.assertEqual(list(self.workspace.iterdir()), [self.binary])

    def test_failed_private_download_does_not_reuse_or_replace_old_binary(self):
        def fail(command):
            Path(command[command.index("--output") + 1]).write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, command)

        self.ctx.sh.side_effect = fail
        with self.assertRaises(subprocess.CalledProcessError):
            download_os_binary(self.ctx, "v0.5.5", "", "matter-labs/zksync-os-private")
        self.assertEqual(self.binary.read_bytes(), b"old release")
        self.assertEqual(list(self.workspace.iterdir()), [self.binary])

    @patch("lib.utils.urllib.request.urlopen")
    def test_legacy_url_download_replaces_cached_binary(self, urlopen):
        response = io.BytesIO(b"new public release")
        response.status = 200
        urlopen.return_value = response
        download_os_binary(self.ctx, "v0.5.0", "https://example.test/os/", None)
        self.assertEqual(self.binary.read_bytes(), b"new public release")
        urlopen.assert_called_once_with(
            "https://example.test/os/releases/download/v0.5.0/multiblock_batch.bin"
        )
        self.ctx.sh.assert_not_called()

    @patch("lib.utils.urllib.request.urlopen", side_effect=OSError("download failed"))
    def test_failed_public_download_stops_before_generation(self, _urlopen):
        with self.assertRaises(OSError):
            download_os_binary(self.ctx, "v0.5.0", "https://example.test/os", None)
        self.assertEqual(self.binary.read_bytes(), b"old release")


class WorkflowVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(
            (
                Path(__file__).resolve().parents[1] / ".github/workflows/update-vk.yaml"
            ).read_text()
        )
        cls.selection = cls.workflow["jobs"]["check-vk"]["steps"][0]

    def select(self, protocol, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "outputs"
            env = {key: "" for key in self.selection["env"]}
            env.update(self.workflow["env"])
            env.update(PROTOCOL_VERSION=protocol, GITHUB_OUTPUT=str(output))
            env.update(overrides)
            result = subprocess.run(
                ["bash", "-eu", "-c", self.selection["run"]],
                env=env,
                capture_output=True,
                text=True,
            )
            values = (
                dict(line.split("=", 1) for line in output.read_text().splitlines())
                if output.exists()
                else {}
            )
            return result, values

    def test_existing_protocol_defaults(self):
        for protocol, tag, contracts in [
            ("v30.2", "v0.2.5", "zksync-os-stable"),
            ("v31.0", "v0.3.0", "draft-v31"),
            ("v32.0", "v0.5.0", "draft-v32"),
        ]:
            with self.subTest(protocol=protocol):
                result, values = self.select(protocol)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["zksync_os_tag"], tag)
                self.assertEqual(values["era_contracts_version"], contracts)
                self.assertEqual(values["zkos_wrapper_version"], "main")
                self.assertEqual(
                    values["zksync_os_repository"], "matter-labs/zksync-os"
                )
                self.assertEqual(
                    values["zkos_wrapper_repository"], "matter-labs/zkos-wrapper"
                )
                self.assertEqual(values["zkos_wrapper_recursion_mode"], "")

    def test_v33_1_defaults(self):
        result, values = self.select(
            "v33.1",
            WRAPPER_VERSION="prover-wrapper-sha",
            CONTRACTS_BRANCH="contracts-v33.1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["zksync_os_tag"], "v0.5.5")
        self.assertEqual(
            values["zksync_os_repository"], "matter-labs/zksync-os-private"
        )
        self.assertEqual(
            values["zkos_wrapper_repository"], "matter-labs/zksync-protocol-private"
        )
        self.assertEqual(values["zkos_wrapper_version"], "prover-wrapper-sha")
        self.assertEqual(values["era_contracts_version"], "contracts-v33.1")
        self.assertEqual(values["zkos_wrapper_layout"], "monorepo")

    def test_v33_1_requires_wrapper_and_contracts(self):
        for overrides, missing in [
            ({"CONTRACTS_BRANCH": "contracts"}, "wrapper revision"),
            ({"WRAPPER_VERSION": "wrapper"}, "contracts branch"),
        ]:
            with self.subTest(missing=missing):
                result, _ = self.select("v33.1", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stderr)

    def test_v33_2_reuses_os_and_pins_new_wrapper(self):
        result, values = self.select("v33.2", CONTRACTS_BRANCH="contracts-v33")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["zksync_os_tag"], "v0.5.5")
        self.assertEqual(
            values["zksync_os_repository"], "matter-labs/zksync-os-private"
        )
        self.assertEqual(
            values["zkos_wrapper_repository"], "matter-labs/zksync-protocol-private"
        )
        self.assertEqual(
            values["zkos_wrapper_version"], "f28fbaac166383ef19cabb96ea4c7201a8096cc3"
        )
        self.assertEqual(values["zkos_wrapper_layout"], "monorepo")
        self.assertEqual(values["zkos_wrapper_crs_power"], "24")
        result, _ = self.select("v33.2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contracts branch", result.stderr)

    def test_explicit_overrides(self):
        result, values = self.select(
            "v33.1",
            WRAPPER_VERSION="wrapper-sha",
            CONTRACTS_BRANCH="contracts",
            OS_TAG="different-tag",
            OS_REPOSITORY="owner/os",
            WRAPPER_REPOSITORY="owner/wrapper",
            WRAPPER_RECURSION_MODE="other-mode",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["zksync_os_tag"], "different-tag")
        self.assertEqual(values["zksync_os_repository"], "owner/os")
        self.assertEqual(values["zkos_wrapper_repository"], "owner/wrapper")
        self.assertEqual(values["zkos_wrapper_recursion_mode"], "other-mode")

    def test_unsupported_protocol(self):
        result, _ = self.select("v99.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported protocol", result.stdout)


class VkGenerationTests(unittest.TestCase):
    @patch("scripts.update_vk.utils.require_cmds")
    @patch("scripts.update_vk.utils.download")
    @patch("scripts.update_vk.download_os_binary")
    def test_generation_and_hash_artifacts(self, download_binary, _download, _require):
        for layout, mode, crs_power in [
            ("legacy", "", "24"),
            ("legacy", "use-reduced-log23-machine", "24"),
            ("monorepo", "", "25"),
            ("monorepo", "", "24"),
        ]:
            with (
                self.subTest(layout=layout, mode=mode),
                tempfile.TemporaryDirectory(prefix="vk test ") as tmp,
            ):
                workspace = Path(tmp)
                contracts = workspace / "contracts"
                wrapper = workspace / "wrapper"
                data = contracts / "tools/verifier-gen/data"
                data.mkdir(parents=True)
                vk_hash = "0x" + "42" * 32
                verifiers = (
                    contracts / "l1-contracts/contracts/state-transition/verifiers"
                )
                verifiers.mkdir(parents=True)
                names = ["ZKsyncOSVerifierPlonk"]
                if layout == "legacy":
                    names.append("ZKsyncOSVerifierFflonk")
                for name in names:
                    (verifiers / f"{name}.sol").write_text("old verifier")
                    (data / f"{name}.sol").write_text(
                        f"/// @dev Contract was generated from a verification key with a hash of {vk_hash}\n"
                    )
                download_binary.side_effect = (
                    lambda ctx, tag, url, repository, asset="multiblock_batch.bin": (
                        workspace / asset
                    )
                )
                commands = []

                def run(command, **kwargs):
                    commands.append(command)
                    if isinstance(command, list) and "generate-snark-vk" in command:
                        (workspace / "snark_vk_expected.json").write_text(
                            '{"generated":true}'
                        )

                    if isinstance(command, list) and "generate-vk" in command:
                        self.assertEqual(kwargs["cwd"], wrapper / "zkos-wrapper")
                        (workspace / "snark_vk.json").write_text('{"generated":true}')

                ctx = MagicMock(
                    workspace=workspace,
                    repo_dir=contracts,
                    logger=logging.getLogger("test"),
                )
                ctx.sh.side_effect = run
                with patch.dict(
                    os.environ,
                    {
                        "ZKOS_WRAPPER_PATH": str(wrapper),
                        "ZKSYNC_OS_TAG": "v0.5.5",
                        "ZKSYNC_OS_REPOSITORY": "matter-labs/zksync-os-private",
                        "ZKOS_WRAPPER_RECURSION_MODE": mode,
                        "ZKOS_WRAPPER_LAYOUT": layout,
                        "ZKOS_WRAPPER_CRS_POWER": crs_power,
                    },
                    clear=True,
                ):
                    script(ctx)
                command = commands[0]
                if layout == "monorepo":
                    args = command[command.index("--") + 1 :]
                    self.assertEqual(
                        args[args.index("--bin") + 1],
                        str(workspace / "multiblock_batch.bin"),
                    )
                    self.assertEqual(
                        args[args.index("--text") + 1],
                        str(workspace / "multiblock_batch.text"),
                    )
                    self.assertEqual(
                        args[args.index("--trusted-setup") + 1],
                        str(
                            workspace
                            / ("setup_2_25.key" if crs_power == "25" else "setup.key")
                        ),
                    )
                    self.assertIn("--check-aux-params", args)
                    self.assertIn("--no-default-features", command)
                    self.assertEqual(
                        command[command.index("--features") + 1], "security_100"
                    )
                else:
                    self.assertEqual(
                        command[command.index("--input-binary") + 1],
                        str(workspace / "multiblock_batch.bin"),
                    )
                self.assertEqual("--recursion-mode" in command, bool(mode))
                if mode:
                    self.assertEqual(
                        command[command.index("--recursion-mode") + 1], mode
                    )
                self.assertEqual(
                    (workspace / "vk_hash.txt").read_text(), vk_hash + "\n"
                )
                for name in names:
                    self.assertEqual(
                        (verifiers / f"{name}.sol").read_text(),
                        (data / f"{name}.sol").read_text(),
                    )
                if layout == "monorepo":
                    self.assertFalse(
                        (verifiers / "ZKsyncOSVerifierFflonk.sol").exists()
                    )
                self.assertEqual(
                    (data / "ZKsyncOS_plonk_scheduler_key.json").read_text(),
                    '{"generated":true}',
                )


if __name__ == "__main__":
    unittest.main()
