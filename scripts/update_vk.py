#!/usr/bin/env python3

import os
from pathlib import Path
import resource
import tempfile

from lib.script_context import ScriptCtx
from lib.entry import run_script
import lib.utils as utils
import lib.config as config

WRAPPER_STACK_BYTES = 256 * 1024 * 1024


def prepare_wrapper_stack() -> None:
    # RUST_MIN_STACK only affects spawned threads; VK synthesis also recurses on main.
    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    if soft != resource.RLIM_INFINITY and soft < WRAPPER_STACK_BYTES:
        resource.setrlimit(resource.RLIMIT_STACK, (WRAPPER_STACK_BYTES, hard))


def download_os_binary(
    ctx: ScriptCtx,
    tag: str,
    url: str,
    repository: str | None,
    asset_name: str = "multiblock_batch.bin",
) -> Path:
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
    wrapper_layout = os.environ.get("ZKOS_WRAPPER_LAYOUT", "legacy")
    if wrapper_layout not in {"legacy", "monorepo"}:
        raise ValueError(f"Unsupported wrapper layout: {wrapper_layout}")
    if wrapper_layout == "monorepo":
        if recursion_mode:
            raise ValueError(
                "The monorepo wrapper uses the unified verifier; omit ZKOS_WRAPPER_RECURSION_MODE"
            )
        zkos_wrapper_path /= "zkos-wrapper"
    if zksync_os_repository:
        utils.require_cmds({"gh": ">=2.0"})

    # ------------------------------------------------------------------ #
    # Download CRS (trusted setup) file
    # ------------------------------------------------------------------ #
    with ctx.section("Download CRS file", expected=30):
        if wrapper_layout == "monorepo":
            # The 100-bit wrapper's SNARK domain has 2^25 rows.
            crs_path = ctx.workspace / "setup_2_25.key"
            crs_url = config.CRS_FILE_2_25_URL
            crs_checksum = config.CRS_FILE_2_25_SHA256_CHECKSUM
        else:
            crs_path = ctx.workspace / "setup.key"
            crs_url = config.CRS_FILE_URL
            crs_checksum = config.CRS_FILE_SHA256_CHECKSUM
        utils.download(
            crs_url,
            crs_path,
            checksum=crs_checksum,
        )

    # ------------------------------------------------------------------ #
    # Download ZKsync OS binary (multiblock_batch.bin) for given tag
    # ------------------------------------------------------------------ #
    with ctx.section("Download ZKsync OS binary", expected=1):
        binary_path = download_os_binary(
            ctx, zksync_os_tag, zksync_os_url, zksync_os_repository
        )
        if wrapper_layout == "monorepo":
            text_path = download_os_binary(
                ctx,
                zksync_os_tag,
                zksync_os_url,
                zksync_os_repository,
                "multiblock_batch.text",
            )

    # ------------------------------------------------------------------ #
    # Generate SNARK VK using zkos-wrapper
    # ------------------------------------------------------------------ #
    with ctx.section("Generate SNARK VK", expected=430):
        prepare_wrapper_stack()
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
        if wrapper_layout == "monorepo":
            # Match the prover's 100-bit circuits and constrain the application commitment.
            # Without --check-aux-params this CLI generates an application-independent VK.
            command = [
                "cargo",
                "run",
                "--locked",
                "--release",
                "-p",
                "zkos-wrapper",
                "--bin",
                "wrapper",
                "--no-default-features",
                "--features",
                "security_100",
                "--",
                "generate-vk",
                "--bin",
                str(binary_path),
                "--text",
                str(text_path),
                "--trusted-setup",
                str(crs_path),
                "--check-aux-params",
                "--output-dir",
                str(ctx.workspace),
            ]
            (ctx.workspace / "snark_vk.json").unlink(missing_ok=True)
        ctx.sh(
            command,
            cwd=zkos_wrapper_path,
            env={
                "RUST_MIN_STACK": os.environ.get(
                    "RUST_MIN_STACK", str(WRAPPER_STACK_BYTES)
                )
            },
        )
        if wrapper_layout == "monorepo":
            utils.cp(ctx.workspace / "snark_vk.json", vk_path)

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
