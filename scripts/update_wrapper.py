#!/usr/bin/env python3

from lib.ctx import ScriptCtx
from lib.entry import run_script
import lib.utils as utils


def script(ctx: ScriptCtx) -> None:

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    ctx.require_cmds(
        {
            "cargo": ">=1.89",
        }
    )

    # ------------------------------------------------------------------ #
    with ctx.section("Building wrapper", expected=10):
        ctx.sh("cargo run --release --bin wrapper_generator")

    # ------------------------------------------------------------------ #
    with ctx.section("Generating fibonacci SNARK proof", expected=285):
        airbender_dir = ctx.path_from_env("ZKSYNC_AIRBENDER_PATH", "zksync-airbender")
        ctx.sh(
            f"""
            cargo run -p cli --release prove
              --bin examples/hashed_fibonacci/app.bin
              --input-file examples/hashed_fibonacci/input.txt
              --until final-proof
              --output-dir {ctx.tmp_dir}
            """,
            cwd=airbender_dir
        )
        generated_json = ctx.tmp_dir / "final_program_proof.json"
        risc_proof = ctx.repo_dir / "wrapper" / "testing_data" / "risc_proof"
        utils.cp(generated_json, risc_proof)

    # ------------------------------------------------------------------ #
    with ctx.section("Updating test data", expected=1300):
        ctx.sh(
            "cargo test --release all_layers_full_test -- --nocapture",
            env={"RUST_MIN_STACK": "67108864"},
        )

if __name__ == "__main__":
    run_script(script)
