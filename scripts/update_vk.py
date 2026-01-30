#!/usr/bin/env python3

from lib.script_context import ScriptCtx
from lib.entry import run_script
import lib.utils as utils
import lib.config as config


def script(ctx: ScriptCtx) -> None:
    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "cargo": ">=1.89",
        }
    )

    # ------------------------------------------------------------------ #
    # Required environment
    # ------------------------------------------------------------------ #
    zkos_wrapper_path = utils.require_path("ZKOS_WRAPPER_PATH")
    zksync_os_tag = utils.require_env("ZKSYNC_OS_TAG")
    zksync_os_url = utils.require_env("ZKSYNC_OS_URL", config.ZKSYNC_OS_URL)

    # ------------------------------------------------------------------ #
    # Download CRS (trusted setup) file
    # ------------------------------------------------------------------ #
    with ctx.section("Download CRS file", expected=30):
        crs_path = ctx.workspace / "setup.key"
        utils.download(
            config.CRS_FILE_URL,
            crs_path,
            checksum=config.CRS_FILE_SHA256_CHECKSUM,
        )

    # ------------------------------------------------------------------ #
    # Download ZKsync OS binary (multiblock_batch.bin) for given tag
    # ------------------------------------------------------------------ #
    with ctx.section("Download ZKsync OS binary", expected=1):
        asset_name = "multiblock_batch.bin"
        asset_url = f"{zksync_os_url}/releases/download/{zksync_os_tag}/{asset_name}"
        output_file = ctx.workspace / asset_name
        utils.download(asset_url, output_file)

    # ------------------------------------------------------------------ #
    # Generate SNARK VK using zkos-wrapper
    # ------------------------------------------------------------------ #
    with ctx.section("Generate SNARK VK", expected=430):
        vk_path = ctx.workspace / "snark_vk_expected.json"
        if vk_path.is_file():
            vk_path.unlink()
        ctx.sh(
            f"""
            cargo run --bin wrapper --release -- \
              generate-snark-vk
              --input-binary {ctx.workspace / "multiblock_batch.bin"}
              --trusted-setup-file {ctx.workspace / "setup.key"}
              --output-dir {ctx.workspace}
            """,
            cwd=zkos_wrapper_path,
        )

    # ------------------------------------------------------------------ #
    # Copy VK and generate verifier contracts
    # ------------------------------------------------------------------ #
    with ctx.section("Copy VK and generate verifier contracts", expected=170):
        # Copy VK JSON into verifier-gen data
        target_vk_json = (
            ctx.repo_dir
            / "tools"
            / "verifier-gen"
            / "data"
            / "ZKsyncOS_plonk_scheduler_key.json"
        )
        utils.cp(ctx.workspace / "snark_vk_expected.json", target_vk_json)

        # Generate verifier contracts
        ctx.sh(
            "cargo run --bin zksync_verifier_contract_generator \
                --release -- --variant zksync-os",
            cwd=ctx.repo_dir / "tools" / "verifier-gen",
        )

        # Copy generated contracts into l1-contracts
        verifiers_dir = (
            ctx.repo_dir
            / "l1-contracts"
            / "contracts"
            / "state-transition"
            / "verifiers"
        )
        for contract in ("ZKsyncOSVerifierPlonk", "ZKsyncOSVerifierFflonk"):
            src = ctx.repo_dir / "tools" / "verifier-gen" / "data" / f"{contract}.sol"
            dst = verifiers_dir / f"{contract}.sol"
            utils.cp(src, dst)

    # ------------------------------------------------------------------ #
    # Update test hashes
    # ------------------------------------------------------------------ #
    with ctx.section("Update test hashes", expected=120):
        ctx.sh(f"yarn --cwd {ctx.repo_dir / "da-contracts"} build:foundry")
        ctx.sh(f"yarn --cwd {ctx.repo_dir / "l1-contracts"} build:foundry")
        ctx.sh(f"yarn --cwd {ctx.repo_dir / "l2-contracts"} build:foundry")
        ctx.sh(f"yarn --cwd {ctx.repo_dir / "system-contracts"} build:foundry")
        ctx.sh(f"yarn calculate-hashes:fix")

if __name__ == "__main__":
    run_script(script)
