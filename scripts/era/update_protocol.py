#!/usr/bin/env python3

from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils


def script(ctx: ScriptCtx) -> None:
    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "cargo": f">=1.80",
        }
    )

    # ------------------------------------------------------------------ #
    # Regenerate reference VKs
    # ------------------------------------------------------------------ #
    vk_checker = ctx.repo_dir / "target" / "release" / "vk_regression_checker"
    keys_dir = ctx.repo_dir / "crates" / "vk_regression_checker" / "reference"

    with ctx.section("Build vk checker binary", expected=120):
        ctx.sh("cargo build -p vk_regression_checker --release")

    with ctx.section("Run vk checker", expected=120):
        ctx.sh(f"{vk_checker} generate --keys-dir {keys_dir} --jobs 1")


if __name__ == "__main__":
    run_script(script)
