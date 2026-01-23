#!/usr/bin/env python3

from lib.script_context import ScriptCtx
from lib.entry import run_script
import lib.utils as utils


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
    with ctx.section("Building wrapper", expected=100):
        ctx.sh("cargo run --release --bin wrapper_generator")

    # ------------------------------------------------------------------ #
    with ctx.section("Generating fibonacci SNARK proof", expected=350):
        airbender_dir = utils.require_path("ZKSYNC_AIRBENDER_PATH")
        ctx.sh(
            f"""
            cargo run -p cli --release prove
              --bin examples/hashed_fibonacci/app.bin
              --input-file examples/hashed_fibonacci/input.txt
              --until final-proof
              --output-dir {ctx.repo_dir / "wrapper" / "testing_data"}
              --final-proof-name risc_proof
            """,
            cwd=airbender_dir,
        )

    # ------------------------------------------------------------------ #
    with ctx.section("Updating test data", expected=1780):
        ctx.sh(
            "cargo test --release all_layers_full_test -- --nocapture",
            env={"RUST_MIN_STACK": "67108864"},
        )


if __name__ == "__main__":
    run_script(script)
