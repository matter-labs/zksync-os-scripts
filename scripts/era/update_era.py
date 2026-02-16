#!/usr/bin/env python3

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib.protocol_version_era import PROTOCOL_TOOLCHAINS
from pathlib import Path


def _parallelism() -> int:
    raw = os.getenv("PARALLELISM", "1").strip()
    try:
        workers = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid PARALLELISM={raw!r}. Expected integer >= 1.") from exc
    if workers < 1:
        raise ValueError(f"Invalid PARALLELISM={workers}. Expected integer >= 1.")
    return workers


def _run_parallel(items, workers: int, run_item) -> None:
    if workers <= 1:
        for item in items:
            run_item(item)
        return

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_item, item): item for item in items}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise


def script(ctx: ScriptCtx) -> None:

    # Paths & constants
    short_sha = utils.get_short_sha(ctx.repo_dir)
    protocol_version: str = utils.require_env("PROTOCOL_VERSION")
    bellman_cuda_dir: Path = utils.require_path("BELLMAN_CUDA_DIR")
    try:
        toolchain = PROTOCOL_TOOLCHAINS[protocol_version]
    except KeyError:
        raise ValueError(
            f"Unsupported PROTOCOL_VERSION: {protocol_version}. Supported: {list(PROTOCOL_TOOLCHAINS.keys())}"
        )
    cast_forge_version: str = toolchain.cast_forge_version
    cargo_version: str = toolchain.cargo_version
    yarn_version: str = toolchain.yarn_version
    workers = _parallelism()
    ctx.logger.info(f"Parallel workers: {workers}")

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "yarn": f">={yarn_version}",
            "cast": f"=={cast_forge_version}",
            "forge": f"=={cast_forge_version}",
            "cargo": f">={cargo_version}",
            "gsutil": ">=5",
        }
    )

    with ctx.section("Build key_generator binary", expected=120):
        ctx.sh(
            """
            cargo build --features "gpu" --release \
                --bin key_generator \
                --manifest-path prover/crates/bin/vk_setup_data_generator_server_fri/Cargo.toml
            """,
            env={"BELLMAN_CUDA_DIR": str(bellman_cuda_dir)}
        )

    # with ctx.section("Generate verification keys", expected=300):
    #     ctx.sh(
    #         """
    #         ./prover/target/release/key_generator \
    #             generate-vk --path ./prover/data/keys
    #         """
    #     )

    with ctx.section("Generate base layer setup data", expected=300):
        circuits = [*range(1, 20), 255]

        def run_base_layer(circuit: int) -> None:
            ctx.logger.info(f"Generating base setup for circuit {circuit}")
            ctx.sh(
                f"""
                ./prover/target/release/key_generator \
                    generate-sk-gpu basic --numeric-circuit {circuit}
                """
            )

        _run_parallel(circuits, workers, run_base_layer)

    with ctx.section("Generate recursive layer setup data", expected=300):
        circuits = [*range(1, 23), 255]

        def run_recursive_layer(circuit: int) -> None:
            ctx.logger.info(f"Generating recursive setup for circuit {circuit}")
            ctx.sh(
                f"""
                ./prover/target/release/key_generator \
                    generate-sk-gpu recursive --numeric-circuit {circuit}
                """
            )

        _run_parallel(circuits, workers, run_recursive_layer)

    with ctx.section("Generate compressor data", expected=300):
        ctx.sh(
            f"""
            ./prover/target/release/key_generator \
                generate-compressor-data
            """
        )

    # with ctx.section("Generate json for paths", expected=5):
    #     json_content = f"""
    #     {{
    #         "sha": "{short_sha}-gpu",
    #         "us": "gs://matterlabs-setup-data-us/{short_sha}-gpu/",
    #         "europe": "gs://matterlabs-setup-data-europe/{short_sha}-gpu/",
    #         "asia": "gs://matterlabs-setup-data-asia/{short_sha}-gpu/"
    #     }}
    #     """
    #     output_path = ctx.repo_dir / "prover" / "setup-data-gpu-keys.json"
    #     output_path.write_text(json_content.strip())

    # with ctx.section("Upload data to GCP", expected=300):
    #     ctx.sh(
    #         f"""
    #         gsutil -m rsync ./prover/data/keys "gs://matterlabs-setup-data-us/{short_sha}-gpu/"
    #         """,
    #     )

    # with ctx.section("Replicate US -> asia/europe", expected=300):
    #     us = f"gs://matterlabs-setup-data-us/{short_sha}-gpu/"
    #     asia = f"gs://matterlabs-setup-data-asia/{short_sha}-gpu/"
    #     europe = f"gs://matterlabs-setup-data-europe/{short_sha}-gpu/"
    #     targets = [("asia", asia), ("europe", europe)]

    #     def replicate_target(target: tuple[str, str]) -> None:
    #         region, bucket = target
    #         ctx.logger.info(f"Replicating US setup data to {region}")
    #         ctx.sh(f'gsutil -m rsync -r "{us}" "{bucket}"')

    #     _run_parallel(targets, workers, replicate_target)


if __name__ == "__main__":
    run_script(script)
