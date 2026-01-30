#!/usr/bin/env python3

from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib.config import ZKSYNC_OS_URL
import lib.utils as utils


def script(ctx: ScriptCtx) -> None:
    zksync_os_tag = utils.require_env("ZKSYNC_OS_TAG")
    zksync_os_url = utils.require_env("ZKSYNC_OS_URL", ZKSYNC_OS_URL)
    # ------------------------------------------------------------------ #
    # Download ZKsync OS binary (multiblock_batch.bin)
    # ------------------------------------------------------------------ #
    with ctx.section("Download ZKsync OS binary", expected=20):
        asset_name = "multiblock_batch.bin"
        asset_url = f"{zksync_os_url}/releases/download/{zksync_os_tag}/{asset_name}"
        output_file = ctx.workspace / asset_name
        utils.download(asset_url, output_file)

    # ------------------------------------------------------------------ #
    # Copy binary into repository
    # ------------------------------------------------------------------ #
    with ctx.section("Copy binary to repository", expected=5):
        target = ctx.repo_dir / "multiblock_batch.bin"
        utils.cp(output_file, target)


if __name__ == "__main__":
    run_script(script)
