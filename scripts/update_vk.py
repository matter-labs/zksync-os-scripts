#!/usr/bin/env python3

import os
from pathlib import Path
import tempfile

from lib.script_context import ScriptCtx
from lib.entry import run_script
import lib.utils as utils
import lib.config as config


def download_os_binary(
    ctx: ScriptCtx, tag: str, url: str, repository: str | None
) -> Path:
    asset_name = "multiblock_batch.bin"
    output_file = ctx.workspace / asset_name
    # A shared workspace may contain another release's binary. Always fetch the requested
    # asset and only replace the previous file once the download succeeds.
    if repository:
        with tempfile.TemporaryDirectory(dir=ctx.workspace) as tmp:
            downloaded = Path(tmp) / asset_name
            # gh uses the authenticated API asset endpoint for private releases and reuses
            # GH_TOKEN, GITHUB_TOKEN, or the operator's local GitHub CLI credentials.
            ctx.sh(
                [
                    "gh",
                    "release",
                    "download",
                    tag,
                    "--repo",
                    repository,
                    "--pattern",
                    asset_name,
                    "--output",
                    str(downloaded),
                ]
            )
            downloaded.replace(output_file)
    else:
        asset_url = f"{url.rstrip('/')}/releases/download/{tag}/{asset_name}"
        utils.download(asset_url, output_file, force=True)
    return output_file


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
    zksync_os_repository = os.environ.get("ZKSYNC_OS_REPOSITORY")
    recursion_mode = os.environ.get("ZKOS_WRAPPER_RECURSION_MODE")
    if zksync_os_repository:
        utils.require_cmds({"gh": ">=2.0"})

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
        binary_path = download_os_binary(
            ctx, zksync_os_tag, zksync_os_url, zksync_os_repository
        )

    # ------------------------------------------------------------------ #
    # Generate SNARK VK using zkos-wrapper
    # ------------------------------------------------------------------ #
    with ctx.section("Generate SNARK VK", expected=430):
        vk_path = ctx.workspace / "snark_vk_expected.json"
        if vk_path.is_file():
            vk_path.unlink()
        command = [
            "cargo",
            "run",
            "--bin",
            "wrapper",
            "--release",
            "--",
            "generate-snark-vk",
            "--input-binary",
            str(binary_path),
            "--trusted-setup-file",
            str(crs_path),
            "--output-dir",
            str(ctx.workspace),
        ]
        if recursion_mode:
            command.extend(["--recursion-mode", recursion_mode])
        ctx.sh(command, cwd=zkos_wrapper_path)

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

        vk_hash = utils.extract_vk_hash(verifiers_dir / "ZKsyncOSVerifierPlonk.sol")
        (ctx.workspace / "vk_hash.txt").write_text(vk_hash + "\n", encoding="utf-8")
        ctx.logger.info("Generated verification key hash: %s", vk_hash)

    # ------------------------------------------------------------------ #
    # Update test hashes
    # ------------------------------------------------------------------ #
    with ctx.section("Update test hashes", expected=120):
        ctx.sh(f"bash -c {ctx.repo_dir}/recompute_hashes.sh")


if __name__ == "__main__":
    run_script(script)
