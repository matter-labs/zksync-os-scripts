#!/usr/bin/env python3

import hashlib
import json
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
    repository: str,
    asset_name: str = "multiblock_batch.bin",
) -> Path:
    output_file = ctx.workspace / asset_name
    # A shared workspace may contain another release's binary. Always fetch the requested
    # asset and only replace the previous file once the download succeeds.
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
    return output_file


def script(ctx: ScriptCtx) -> None:
    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "cargo": ">=1.89",
            "gh": ">=2.0",
        }
    )

    # ------------------------------------------------------------------ #
    # Required environment
    # ------------------------------------------------------------------ #
    zkos_wrapper_path = utils.require_path("ZKOS_WRAPPER_PATH") / "zkos-wrapper"
    zksync_os_tag = utils.require_env("ZKSYNC_OS_TAG")
    zksync_os_repository = utils.require_env("ZKSYNC_OS_REPOSITORY")
    crs_power = os.environ.get("ZKOS_WRAPPER_CRS_POWER", "25")
    if crs_power not in {"24", "25"}:
        raise ValueError(f"Unsupported CRS power: {crs_power}")

    # ------------------------------------------------------------------ #
    # Download CRS (trusted setup) file
    # ------------------------------------------------------------------ #
    with ctx.section("Download CRS file", expected=30):
        if crs_power == "25":
            # v33.1 needs 2^25 points; PR #49 reduces the domain to 2^22,
            # so v33.2 can use the existing checksum-pinned 2^24 setup.
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
        binary_path = download_os_binary(ctx, zksync_os_tag, zksync_os_repository)
        text_path = download_os_binary(
            ctx, zksync_os_tag, zksync_os_repository, "multiblock_batch.text"
        )

    manifest_path = ctx.workspace / "generation-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest["program_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (binary_path, text_path)
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    # ------------------------------------------------------------------ #
    # Generate SNARK VK using zkos-wrapper
    # ------------------------------------------------------------------ #
    with ctx.section("Generate SNARK VK", expected=430):
        prepare_wrapper_stack()
        vk_path = ctx.workspace / "snark_vk_expected.json"
        if vk_path.is_file():
            vk_path.unlink()
        (ctx.workspace / "snark_vk.json").unlink(missing_ok=True)
        # Match the prover's 100-bit circuits and constrain the application commitment.
        # Without --check-aux-params this CLI generates an application-independent VK.
        ctx.sh(
            [
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
            ],
            cwd=zkos_wrapper_path,
            env={
                "RUST_MIN_STACK": os.environ.get(
                    "RUST_MIN_STACK", str(WRAPPER_STACK_BYTES)
                )
            },
        )
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
        utils.cp(
            ctx.repo_dir
            / "tools"
            / "verifier-gen"
            / "data"
            / "ZKsyncOSVerifierPlonk.sol",
            verifiers_dir / "ZKsyncOSVerifierPlonk.sol",
        )

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
