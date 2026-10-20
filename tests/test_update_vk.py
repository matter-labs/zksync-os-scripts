import base64
import io
import json
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
    recompute_hashes_env,
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


class RecomputeHashesEnvTests(unittest.TestCase):
    def test_keeps_the_inherited_path_without_upstream_foundry(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            self.assertIsNone(recompute_hashes_env())

    def test_puts_upstream_foundry_first_on_path(self):
        with patch.dict(
            os.environ,
            {"PATH": "/usr/bin", "UPSTREAM_FOUNDRY_DIR": "/opt/foundry"},
            clear=True,
        ):
            self.assertEqual(
                recompute_hashes_env(), {"PATH": f"/opt/foundry{os.pathsep}/usr/bin"}
            )


class UpstreamFoundryStepTests(unittest.TestCase):
    STEP = "Install the contracts' pinned upstream Foundry"

    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(
            (
                Path(__file__).resolve().parents[1] / ".github/workflows/update-vk.yaml"
            ).read_text()
        )
        cls.names = [step.get("name") for step in workflow["jobs"]["check-vk"]["steps"]]
        cls.step = next(
            step
            for step in workflow["jobs"]["check-vk"]["steps"]
            if step.get("name") == cls.STEP
        )

    def run_step(self, pin=None, curl=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            workspace = tmp / "workspace"
            (workspace / "era-contracts" / ".github").mkdir(parents=True)
            if pin is not None:
                (
                    workspace / "era-contracts" / ".github" / "foundry-versions.env"
                ).write_text(pin)
            github_env = tmp / "github_env"
            github_env.touch()
            path = os.environ["PATH"]
            if curl is not None:
                stub = tmp / "bin"
                stub.mkdir()
                (stub / "curl").write_text(curl)
                (stub / "curl").chmod(0o755)
                path = f"{stub}{os.pathsep}{path}"
            runner_temp = tmp / "runner"
            runner_temp.mkdir()
            result = subprocess.run(
                ["bash", "-e", "-c", self.step["run"]],
                env={
                    "PATH": path,
                    "WORKSPACE": str(workspace),
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_ENV": str(github_env),
                },
                capture_output=True,
                text=True,
            )
            return result, github_env.read_text(), runner_temp

    def test_runs_after_the_contracts_checkout_and_before_the_update(self):
        position = self.names.index(self.STEP)
        self.assertLess(self.names.index("Checkout contracts"), position)
        self.assertLess(position, self.names.index("Update vk"))

    def test_contracts_without_a_pin_keep_foundry_zksync(self):
        result, github_env, _ = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(github_env, "")

    def test_rejects_a_malformed_pin(self):
        result, github_env, _ = self.run_step(pin="FOUNDRY_VERSION=latest\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected FOUNDRY_VERSION", result.stderr)
        self.assertEqual(github_env, "")

    def test_installs_the_pinned_version_off_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A stand-in release tarball holding a forge that reports its version.
            archive = Path(tmp) / "foundry.tar.gz"
            forge = Path(tmp) / "forge"
            forge.write_text("#!/bin/sh\necho forge Version: 1.5.1-v1.5.1\n")
            forge.chmod(0o755)
            subprocess.run(["tar", "czf", str(archive), "-C", tmp, "forge"], check=True)
            curl = (
                "#!/bin/sh\n"
                'case "$*" in *foundry-rs/foundry/releases/download/v1.5.1/'
                "foundry_v1.5.1_linux_amd64.tar.gz*) "
                f"cat {archive};; *) exit 22;; esac\n"
            )
            result, github_env, runner_temp = self.run_step(
                pin="# comment\nFOUNDRY_VERSION=v1.5.1\nFOUNDRY_COMMIT=b0a9dd9ce\n",
                curl=curl,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("forge Version: 1.5.1-v1.5.1", result.stdout)
        self.assertEqual(
            github_env,
            f"UPSTREAM_FOUNDRY_DIR={runner_temp / 'upstream-foundry-v1.5.1'}\n",
        )


REPO_ROOT = Path(__file__).resolve().parents[1]
PUSH_SIGNED_COMMIT = REPO_ROOT / ".github/scripts/push-signed-commit.sh"
# Stands in for `gh`: logs each call (arguments joined by \x1f, one call per
# line) and answers the API calls push-signed-commit.sh makes.
STUB_GH = """#!/usr/bin/env bash
printf '%s\\x1f' "$@" >> "${STUB_DIR}/gh.log"
printf '\\n' >> "${STUB_DIR}/gh.log"
case "$*" in
  "api graphql --input "*) cp "$4" "${STUB_DIR}/request.json"; echo "${STUB_OID}" ;;
  "api repos/"*"/git/matching-refs/heads/"*) echo "${STUB_CURRENT:-}" ;;
  "api repos/"*"/commits/"*) echo "${STUB_VERIFIED}" ;;
esac
"""


def isolated_git_env():
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }


def write_stub_gh(tmp):
    stub_bin = tmp / "bin"
    stub_bin.mkdir()
    (stub_bin / "gh").write_text(STUB_GH)
    (stub_bin / "gh").chmod(0o755)
    return f"{stub_bin}{os.pathsep}{os.environ['PATH']}"


def gh_calls(stub_dir):
    log = stub_dir / "gh.log"
    if not log.exists():
        return []
    return [line.split("\x1f")[:-1] for line in log.read_text().splitlines()]


class PushSignedCommitScriptTests(unittest.TestCase):
    REPO = "matter-labs/era-contracts"
    BASE = "b" * 40
    OID = "c" * 40

    def run_script(self, expected_old="", current="", verified="true"):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stub_dir = tmp / "stub"
            stub_dir.mkdir()
            work = tmp / "work"
            (work / "dir").mkdir(parents=True)
            (work / "dir" / "a.txt").write_text("new contents\n")
            (work / "b.bin").write_bytes(bytes(range(256)) * 3)
            message = tmp / "message"
            message.write_text("headline\n\nbody line\n")
            result = subprocess.run(
                [
                    "bash",
                    str(PUSH_SIGNED_COMMIT),
                    "target",
                    self.BASE,
                    str(message),
                    expected_old,
                    "dir/a.txt",
                    "b.bin",
                    "gone.txt",
                ],
                cwd=work,
                env={
                    "PATH": write_stub_gh(tmp),
                    "GH_TOKEN": "token",
                    "TARGET_REPOSITORY": self.REPO,
                    "STUB_DIR": str(stub_dir),
                    "STUB_OID": self.OID,
                    "STUB_CURRENT": current,
                    "STUB_VERIFIED": verified,
                },
                capture_output=True,
                text=True,
            )
            request_path = stub_dir / "request.json"
            request = (
                json.loads(request_path.read_text()) if request_path.exists() else None
            )
            return result, gh_calls(stub_dir), request

    @staticmethod
    def ref_writes(calls):
        return [call for call in calls if call[2:3] in (["POST"], ["PATCH"])]

    def assert_temp_branch_deleted(self, calls, request):
        temp = request["variables"]["input"]["branch"]["branchName"]
        self.assertEqual(
            calls[-1],
            ["api", "--method", "DELETE", f"repos/{self.REPO}/git/refs/heads/{temp}"],
        )

    def test_creates_a_new_branch_at_a_signed_commit(self):
        result, calls, request = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.OID)

        commit = request["variables"]["input"]
        temp = commit["branch"]["branchName"]
        self.assertTrue(temp.startswith("automation/signed-commit-"))
        self.assertEqual(commit["branch"]["repositoryNameWithOwner"], self.REPO)
        self.assertEqual(commit["expectedHeadOid"], self.BASE)
        self.assertEqual(
            commit["message"], {"headline": "headline", "body": "body line"}
        )
        additions = {
            entry["path"]: base64.b64decode(entry["contents"])
            for entry in commit["fileChanges"]["additions"]
        }
        self.assertEqual(
            additions,
            {"dir/a.txt": b"new contents\n", "b.bin": bytes(range(256)) * 3},
        )
        self.assertEqual(commit["fileChanges"]["deletions"], [{"path": "gone.txt"}])

        # The temp branch starts at BASE; the target is created at the new commit.
        refs = f"repos/{self.REPO}/git/refs"
        self.assertEqual(
            self.ref_writes(calls),
            [
                ["api", "--method", "POST", refs, "-f", f"ref=refs/heads/{temp}"]
                + ["-f", f"sha={self.BASE}"],
                ["api", "--method", "POST", refs, "-f", "ref=refs/heads/target"]
                + ["-f", f"sha={self.OID}"],
            ],
        )
        self.assert_temp_branch_deleted(calls, request)

    def test_moves_an_existing_branch_under_its_lease(self):
        result, calls, request = self.run_script(
            expected_old="a" * 40, current="a" * 40
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.ref_writes(calls)[-1],
            ["api", "--method", "PATCH", f"repos/{self.REPO}/git/refs/heads/target"]
            + ["-f", f"sha={self.OID}", "-F", "force=true"],
        )
        self.assert_temp_branch_deleted(calls, request)

    def test_refuses_a_stale_lease(self):
        result, calls, request = self.run_script(
            expected_old="a" * 40, current="d" * 40
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stderr)
        # Only the temp branch was written.
        self.assertEqual(len(self.ref_writes(calls)), 1)
        self.assert_temp_branch_deleted(calls, request)

    def test_refuses_an_unsigned_commit(self):
        result, calls, request = self.run_script(verified="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not sign", result.stderr)
        self.assertEqual(len(self.ref_writes(calls)), 1)
        self.assert_temp_branch_deleted(calls, request)


class CommitStepTests(unittest.TestCase):
    STEP = "Commit era-contracts changes"
    TITLE = "chore: update verification keys for protocol v33.2"

    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/update-vk.yaml").read_text()
        )
        cls.step = next(
            step
            for step in workflow["jobs"]["check-vk"]["steps"]
            if step.get("name") == cls.STEP
        )

    def run_step(self, open_pr, change):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stub_dir = tmp / "stub"
            stub_dir.mkdir()
            # Records its arguments and the message file in place of the real script.
            push = tmp / "push-signed-commit.sh"
            push.write_text(
                "#!/usr/bin/env bash\n"
                'printf \'%s\\x1f\' "$@" > "${STUB_DIR}/push.args"\n'
                'cp "$3" "${STUB_DIR}/push.message"\n'
            )
            repo = tmp / "era-contracts"
            repo.mkdir()
            git_env = {**os.environ, **isolated_git_env()}
            for name in ("a.txt", "old.txt", "gone.txt"):
                (repo / name).write_text(f"{name}\n")
            for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
                subprocess.run(["git", *args], cwd=repo, env=git_env, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            change(repo)
            result = subprocess.run(
                ["bash", "-e", "-c", self.step["run"]],
                cwd=repo,
                env={
                    **isolated_git_env(),
                    "PATH": write_stub_gh(tmp),
                    "STUB_DIR": str(stub_dir),
                    "GH_TOKEN": "token",
                    "TARGET_REPOSITORY": "matter-labs/era-contracts",
                    "PUSH_SIGNED_COMMIT": str(push),
                    "CONTRACTS_BRANCH": "draft/v0.34.0",
                    "OPEN_PR": open_pr,
                    "PROTOCOL_VERSION": "v33.2",
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
            return result, base, push_args, message, gh_calls(stub_dir)

    @staticmethod
    def change_files(repo):
        (repo / "a.txt").write_text("changed\n")
        (repo / "added.txt").write_text("added\n")
        (repo / "old.txt").rename(repo / "new.txt")
        (repo / "gone.txt").unlink()

    def test_never_commits_or_pushes_with_git(self):
        self.assertNotRegex(self.step["run"], r"git (commit|push)\b")

    def test_skips_when_nothing_changed(self):
        result, _, push_args, _, calls = self.run_step("true", lambda repo: None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(push_args)
        self.assertEqual(calls, [])

    def test_opens_a_pr_from_a_new_signed_branch(self):
        result, base, push_args, message, calls = self.run_step(
            "true", self.change_files
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        branch, parent, _, expected_old, *paths = push_args
        self.assertRegex(branch, r"^update-vk-[0-9]+$")
        self.assertEqual(parent, base)
        self.assertEqual(expected_old, "")
        # A rename lists both paths, so the old one is deleted.
        self.assertEqual(
            sorted(paths), ["a.txt", "added.txt", "gone.txt", "new.txt", "old.txt"]
        )
        self.assertEqual(message, f"{self.TITLE}\n")
        self.assertEqual(
            calls,
            [
                ["pr", "create", "--repo", "matter-labs/era-contracts"]
                + ["--title", self.TITLE]
                + ["--body", "Automated verification keys update for protocol v33.2"]
                + ["--base", "draft/v0.34.0", "--head", branch]
            ],
        )

    def test_pushes_onto_the_contracts_branch_without_a_pr(self):
        result, base, push_args, _, calls = self.run_step("false", self.change_files)
        self.assertEqual(result.returncode, 0, result.stderr)
        branch, parent, _, expected_old, *_ = push_args
        self.assertEqual(branch, "draft/v0.34.0")
        self.assertEqual(parent, base)
        # Moving the branch requires it to still be at the commit the update ran on.
        self.assertEqual(expected_old, base)
        self.assertEqual(calls, [])


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
