#!/usr/bin/env python3

import subprocess
import time
from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib.protocol_version_era import PROTOCOL_TOOLCHAINS
from lib import config


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
        }
    )

    bellman_cuda_dir = utils.prepare_bellman_cuda(ctx)

    # ------------------------------------------------------------------ #
    # Build key generator
    # ------------------------------------------------------------------ #
    key_generator_path = (
        ctx.repo_dir / "prover" / "target" / "release" / "key_generator"
    )
    key_generator_manifest_path = (
        ctx.repo_dir
        / "prover"
        / "crates"
        / "bin"
        / "vk_setup_data_generator_server_fri"
        / "Cargo.toml"
    )
    with ctx.section("Build key_generator binary", expected=120):
        ctx.sh(
            f"""
            cargo build --features "gpu" --release \
                --bin key_generator \
                --manifest-path {key_generator_manifest_path}
            """,
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
        crs_path = ctx.workspace / "setup.key"
        utils.download(
            config.CRS_FILE_URL,
            crs_path,
            checksum=config.CRS_FILE_SHA256_CHECKSUM,
        )
        ctx.sh(
            f"{key_generator_path} generate-compressor-data",
            env={"COMPACT_CRS_FILE": str(crs_path)},
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

    # TODO: uncomment after testing
    # with ctx.section("Upload data to GCP", expected=300):
    #     ctx.sh(f"gsutil -m rsync ./prover/data/keys {us}")
    #     ctx.sh(f"gsutil -m rsync -r {us} {asia}")
    #     ctx.sh(f"gsutil -m rsync -r {us} {europe}")

    with ctx.section("Update contracts with new verifier", expected=120):
        # Copy the generated verification keys to the expected location for the contract generator
        ctx.sh(
            f"cp {ctx.repo_dir}/prover/data/keys/fflonk_verification_snark_key.json {ctx.repo_dir}/contracts/tools/data/fflonk_scheduler_key.json"
        )
        ctx.sh(
            f"cp {ctx.repo_dir}/prover/data/keys/verification_snark_key.json {ctx.repo_dir}/contracts/tools/data/plonk_scheduler_key.json"
        )
        # Re-generate the verifier contracts with the new keys
        ctx.sh(
            f"""
            cargo run --bin zksync_verifier_contract_generator --release -- \
                --plonk_input_path {ctx.repo_dir}/contracts/tools/data/plonk_scheduler_key.json \
                --fflonk_input_path {ctx.repo_dir}/contracts/tools/data/fflonk_scheduler_key.json \
                --plonk_output_path {ctx.repo_dir}/l1-contracts/contracts/state-transition/verifiers/L1VerifierPlonk.sol \
                --fflonk_output_path {ctx.repo_dir}/l1-contracts/contracts/state-transition/verifiers/L1VerifierFflonk.sol
            """,
            cwd=ctx.repo_dir / "contracts" / "tools",
        )
        # For L2 verifiers, we need to use the --l2_mode flag to generate the correct contracts
        ctx.sh(
            f"""
            cargo run --bin zksync_verifier_contract_generator --release -- \
                --l2_mode \
                --plonk_input_path {ctx.repo_dir}/contracts/tools/data/plonk_scheduler_key.json \
                --fflonk_input_path {ctx.repo_dir}/contracts/tools/data/fflonk_scheduler_key.json \
                --plonk_output_path {ctx.repo_dir}/l1-contracts/contracts/state-transition/verifiers/L2VerifierPlonk.sol \
                --fflonk_output_path {ctx.repo_dir}/l1-contracts/contracts/state-transition/verifiers/L2VerifierFflonk.sol
            """,
            cwd=ctx.repo_dir / "contracts" / "tools",
        )
        # Recompute hashes
        ctx.sh("./recompute_hashes.sh", cwd=ctx.repo_dir / "contracts")

    with ctx.section("Commit and create PR for verifier contract changes", expected=30):
        contracts_dir = ctx.repo_dir / "contracts"
        branch = f"update-verifiers-{int(time.time())}"

        has_changes = False
        try:
            ctx.sh(
                ["git", "diff", "--exit-code", "--quiet"],
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
                ["git", "config", "user.name", "zksync-admin-bot2"],
                cwd=contracts_dir,
            )
            ctx.sh(
                ["git", "config", "user.email", "temp-bot@matterlabs.dev"],
                cwd=contracts_dir,
            )
            ctx.sh(["git", "checkout", "-b", branch], cwd=contracts_dir)
            ctx.sh(
                [
                    "git",
                    "add",
                    "l1-contracts/contracts/state-transition/verifiers",
                    "tools/data",
                    "AllContractsHashes.json",
                ],
                cwd=contracts_dir,
            )
            ctx.sh(
                [
                    "git",
                    "commit",
                    "-m",
                    "chore: update verifier contracts with new keys",
                ],
                cwd=contracts_dir,
            )
            # TODO: uncomment after testing
            # ctx.sh(
            #     ["git", "push", "--set-upstream", "origin", branch],
            #     cwd=contracts_dir,
            # )
            # ctx.sh(
            #     [
            #         "gh",
            #         "pr",
            #         "create",
            #         "--title",
            #         "chore: update verifier contracts with new keys",
            #         "--body",
            #         "Update verifier contracts with new keys",
            #         "--base",
            #         "main",
            #         "--head",
            #         branch,
            #     ],
            #     cwd=contracts_dir,
            # )

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
