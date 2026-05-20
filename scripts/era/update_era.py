#!/usr/bin/env python3

import os
import subprocess
import time
from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib.protocol_version_era import PROTOCOL_TOOLCHAINS
from lib import config
from lib import edit_server


def script(ctx: ScriptCtx) -> None:
    # ------------------------------------------------------------------ #
    # Paths & constants
    # ------------------------------------------------------------------ #
    protocol_version: str = utils.require_env("PROTOCOL_VERSION", "v30")
    try:
        toolchain = PROTOCOL_TOOLCHAINS[protocol_version]
    except KeyError:
        raise ValueError(
            f"Unsupported PROTOCOL_VERSION: {protocol_version}. Supported: {list(PROTOCOL_TOOLCHAINS.keys())}"
        )
    cast_forge_version: str = toolchain.cast_forge_version
    cargo_version: str = toolchain.cargo_version
    yarn_version: str = toolchain.yarn_version

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "yarn": f">={yarn_version}",
            "cast": f"=={cast_forge_version}",
            "forge": f"=={cast_forge_version}",
            "cargo": f">={cargo_version}",
            "git": ">=2",
            "cmake": ">=3",
            "gsutil": ">=5",
            "gh": ">=2",
            "sqlx": ">=0.8",
        }
    )

    bellman_cuda_dir = utils.prepare_bellman_cuda(ctx)

    # ------------------------------------------------------------------ #
    # Build key generator
    # ------------------------------------------------------------------ #
    key_generator_path = (
        ctx.repo_dir / "prover" / "target" / "release" / "key_generator"
    )

    crs_path = ctx.workspace / "setup_compact.key"
    utils.download(
        config.CRS_FILE_COMPACT_URL,
        crs_path,
        checksum=config.CRS_FILE_COMPACT_SHA256_CHECKSUM,
    )

    with ctx.section("Build key_generator binary", expected=120):
        ctx.sh(
            f"""
            cargo build --features "gpu" --release --bin key_generator
            """,
            cwd=ctx.repo_dir / "prover",
            env={"BELLMAN_CUDA_DIR": str(bellman_cuda_dir)},
        )

    # ------------------------------------------------------------------ #
    # Generate verification keys
    # ------------------------------------------------------------------ #
    with ctx.section("Generate verification keys", expected=300):
        ctx.sh(f"{key_generator_path} generate-vk --path ./prover/data/keys")

    # ------------------------------------------------------------------ #
    # Generate prover setup data
    # ------------------------------------------------------------------ #
    with ctx.section("Generate base layer setup data", expected=300):
        for numeric_circuit in [*range(1, 20), 255]:
            ctx.sh(
                f"{key_generator_path} generate-sk-gpu basic --numeric-circuit {numeric_circuit}"
            )

    with ctx.section("Generate recursive layer setup data", expected=300):
        for numeric_circuit in [*range(1, 23), 255]:
            ctx.sh(
                f"{key_generator_path} generate-sk-gpu recursive --numeric-circuit {numeric_circuit}"
            )

    with ctx.section("Generate compressor data", expected=300):
        ctx.sh(
            f"./prover/target/release/key_generator generate-compressor-data",
            env={
                "COMPACT_CRS_FILE": str(crs_path),
                "ZKSYNC_HOME": str(ctx.repo_dir),
            },
        )

    # ------------------------------------------------------------------ #
    # Generate json and upload data to GCP
    # ------------------------------------------------------------------ #
    setup_data_gpu_keys_json = ctx.repo_dir / "prover" / "setup-data-gpu-keys.json"
    short_sha = utils.get_short_sha(ctx.repo_dir)
    us = f"gs://matterlabs-setup-data-us/{short_sha}-gpu/"
    asia = f"gs://matterlabs-setup-data-asia/{short_sha}-gpu/"
    europe = f"gs://matterlabs-setup-data-europe/{short_sha}-gpu/"
    with ctx.section("Generate json for paths", expected=5):
        json_content = f"""
        {{
            "sha": "{short_sha}-gpu",
            "us": "{us}",
            "europe": "{europe}",
            "asia": "{asia}"
        }}
        """
        setup_data_gpu_keys_json.write_text(json_content.strip())

    with ctx.section("Upload data to GCP", expected=300):
        ctx.sh(f"gsutil -m rsync ./prover/data/keys {us}")
        ctx.sh(f"gsutil -m rsync -r {us} {asia}")
        ctx.sh(f"gsutil -m rsync -r {us} {europe}")

    with ctx.section("Update contracts with new verifier", expected=120):
        # Copy the generated verification keys to the expected location for the contract generator
        ctx.sh(
            f"cp {ctx.repo_dir}/prover/data/keys/fflonk_verification_snark_key.json {ctx.repo_dir}/contracts/tools/verifier-gen/data/Era_fflonk_scheduler_key.json"
        )
        ctx.sh(
            f"cp {ctx.repo_dir}/prover/data/keys/verification_snark_key.json {ctx.repo_dir}/contracts/tools/verifier-gen/data/Era_plonk_scheduler_key.json"
        )
        # Re-generate the verifier contracts with the new keys
        ctx.sh(
            f"""
            cargo run --bin zksync_verifier_contract_generator --release -- --variant era
            """,
            cwd=ctx.repo_dir / "contracts" / "tools" / "verifier-gen",
        )
        ctx.sh(
            f"cp {ctx.repo_dir}/contracts/tools/verifier-gen/data/EraVerifierPlonk.sol {ctx.repo_dir}/contracts/l1-contracts/contracts/state-transition/verifiers/EraVerifierPlonk.sol"
        )
        ctx.sh(
            f"cp {ctx.repo_dir}/contracts/tools/verifier-gen/data/EraVerifierFflonk.sol {ctx.repo_dir}/contracts/l1-contracts/contracts/state-transition/verifiers/EraVerifierFflonk.sol"
        )
        # Recompute hashes
        ctx.sh(
            f"bash -c {ctx.repo_dir}/contracts/recompute_hashes.sh",
            cwd=ctx.repo_dir / "contracts",
        )

    with ctx.section("Commit and create PR for verifier contract changes", expected=30):
        contracts_dir = ctx.repo_dir / "contracts"
        branch = f"update-verifiers-{int(time.time())}"

        has_changes = False
        try:
            ctx.sh(
                [
                    "git",
                    "diff",
                    "--exit-code",
                    "--quiet",
                    "--",
                    "l1-contracts/contracts/state-transition/verifiers",
                    "tools/verifier-gen/data",
                    "AllContractsHashes.json",
                ],
                cwd=contracts_dir,
                print_command=False,
            )
        except subprocess.CalledProcessError:
            has_changes = True

        if not has_changes:
            ctx.logger.info(
                "No verifier contract changes detected, skipping commit/PR."
            )
        else:
            ctx.sh(
                ["git", "config", "user.name", "protocol-upgrade-bot"],
                cwd=contracts_dir,
            )
            ctx.sh(
                ["git", "config", "user.email", "protocol-upgrade-bot@matterlabs.dev"],
                cwd=contracts_dir,
            )
            ctx.sh(["git", "checkout", "-b", branch], cwd=contracts_dir)
            ctx.sh(
                [
                    "git",
                    "add",
                    "-A",
                    "l1-contracts/contracts/state-transition/verifiers",
                    "tools/verifier-gen/data",
                    "AllContractsHashes.json",
                ],
                cwd=contracts_dir,
            )
            has_staged_changes = False
            try:
                ctx.sh(
                    ["git", "diff", "--cached", "--exit-code", "--quiet"],
                    cwd=contracts_dir,
                    print_command=False,
                )
            except subprocess.CalledProcessError:
                has_staged_changes = True

            if not has_staged_changes:
                ctx.logger.info(
                    "No staged verifier changes found after git add, skipping commit."
                )
            else:
                ctx.sh(
                    [
                        "git",
                        "commit",
                        "-m",
                        "chore: update verifier contracts with new keys",
                    ],
                    cwd=contracts_dir,
                )
                ctx.sh(
                    ["git", "push", "--set-upstream", "origin", branch],
                    cwd=contracts_dir,
                )
                ctx.sh(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--title",
                        "chore: update verifier contracts with new keys",
                        "--body",
                        "Update verifier contracts with new keys",
                        "--base",
                        "main",
                        "--head",
                        branch,
                    ],
                    cwd=contracts_dir,
                )

    # ------------------------------------------------------------------ #
    # Regenerate genesis
    # ------------------------------------------------------------------ #

    with ctx.section("Build zkstack binary", expected=120):
        ctx.sh(
            "cargo build --release --bin zkstack",
            cwd=ctx.repo_dir / "zkstack_cli",
        )

    zkstack = ctx.repo_dir / "zkstack_cli" / "target" / "release" / "zkstack"
    with ctx.section("Regenerate genesis", expected=300):
        ctx.sh(f"{zkstack} up -o=false")
        ctx.sh(f"{zkstack} dev contracts")
        ctx.sh(f"{zkstack} chain init configs --dev")
        ctx.sh(f"{zkstack} dev generate-genesis")


if __name__ == "__main__":
    run_script(script)
