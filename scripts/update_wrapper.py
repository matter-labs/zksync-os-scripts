#!/usr/bin/env python3

import json

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
    with ctx.section("Generating fibonacci FRI proof", expected=350):
        airbender_dir = utils.require_path("ZKSYNC_AIRBENDER_PATH")
        testing_data = ctx.repo_dir / "wrapper" / "testing_data"
        artifact_name = "risc_proof_artifact.json"
        # The airbender CLI was reworked (PR #220): `prove` no longer takes
        # `--until final-proof` / `--final-proof-name`. It defaults to the
        # recursion-unified target, which is the old "final proof".
        # CI runs the wrapper tests at security_100 (the default feature),
        # so the fixture to refresh is risc_proof_100sb.
        ctx.sh(
            f"""
            cargo run -p cli --release prove
              --bin examples/hashed_fibonacci/app.bin
              --input-file examples/hashed_fibonacci/input.txt
              --security-level 100
              --output-dir {testing_data}
              --output-file {artifact_name}
            """,
            cwd=airbender_dir,
        )

        # The CLI writes a ProofArtifact JSON (metadata wrapping the proof),
        # but the wrapper tests read a bincode-encoded bare
        # UnrolledProgramProof. Extract the inner proof, then let the
        # wrapper's converter test rewrite it as bincode in place.
        artifact_path = testing_data / artifact_name
        proof_path = testing_data / "risc_proof_100sb"
        artifact = json.loads(artifact_path.read_text())
        proof_path.write_text(json.dumps(artifact["proof"]))
        artifact_path.unlink()
        ctx.sh(
            "cargo test --release convert_risc_proof_from_json -- --ignored --nocapture",
            env={"RUST_MIN_STACK": "16777216"},
        )

    # ------------------------------------------------------------------ #
    with ctx.section("Updating test data", expected=1780):
        ctx.sh(
            "cargo test --release all_layers_full_test -- --nocapture",
            env={"RUST_MIN_STACK": "67108864"},
        )


if __name__ == "__main__":
    run_script(script)
