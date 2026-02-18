#!/usr/bin/env python3

from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib.protocol_version_era import PROTOCOL_TOOLCHAINS


def script(ctx: ScriptCtx) -> None:
    # ------------------------------------------------------------------ #
    # Paths & constants
    # ------------------------------------------------------------------ #
    protocol_version: str = utils.require_env("PROTOCOL_VERSION", "v30")
    try:
        toolchain = PROTOCOL_TOOLCHAINS[protocol_version]
    except KeyError:
        raise ValueError(
            f"Unsupported PROTOCOL_VERSION: {protocol_version}. Supported: {list(PROTOCOL_TOOLCHAINS.keys())}"
        )
    cast_forge_version: str = toolchain.cast_forge_version
    cargo_version: str = toolchain.cargo_version
    yarn_version: str = toolchain.yarn_version

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "yarn": f">={yarn_version}",
            "cast": f"=={cast_forge_version}",
            "forge": f"=={cast_forge_version}",
            "cargo": f">={cargo_version}",
            "git": ">=2",
            "cmake": ">=3",
            "gsutil": ">=5",
        }
    )

    bellman_cuda_dir = utils.prepare_bellman_cuda(ctx)

    # ------------------------------------------------------------------ #
    # Build key generator
    # ------------------------------------------------------------------ #
    key_generator_path = (
        ctx.repo_dir / "prover" / "target" / "release" / "key_generator"
    )
    key_generator_manifest_path = (
        ctx.repo_dir
        / "prover"
        / "crates"
        / "bin"
        / "vk_setup_data_generator_server_fri"
        / "Cargo.toml"
    )
    with ctx.section("Build key_generator binary", expected=120):
        ctx.sh(
            f"""
            cargo build --features "gpu" --release \
                --bin key_generator \
                --manifest-path {key_generator_manifest_path}
            """,
            env={"BELLMAN_CUDA_DIR": str(bellman_cuda_dir)},
        )

    # ------------------------------------------------------------------ #
    # Generate verification keys
    # ------------------------------------------------------------------ #
    with ctx.section("Generate verification keys", expected=300):
        ctx.sh(f"{key_generator_path} generate-vk --path ./prover/data/keys")

    # ------------------------------------------------------------------ #
    # Generate prover setup data
    # ------------------------------------------------------------------ #
    with ctx.section("Generate base layer setup data", expected=300):
        for numeric_circuit in [*range(1, 20), 255]:
            ctx.sh(
                f"{key_generator_path} generate-sk-gpu basic --numeric-circuit {numeric_circuit}"
            )

    with ctx.section("Generate recursive layer setup data", expected=300):
        for numeric_circuit in [*range(1, 23), 255]:
            ctx.sh(
                f"{key_generator_path} generate-sk-gpu recursive --numeric-circuit {numeric_circuit}"
            )

    with ctx.section("Generate compressor data", expected=300):
        ctx.sh(f"{key_generator_path} generate-compressor-data")

    # ------------------------------------------------------------------ #
    # Generate json and upload data to GCP
    # ------------------------------------------------------------------ #
    setup_data_gpu_keys_json = ctx.repo_dir / "prover" / "setup-data-gpu-keys.json"
    short_sha = utils.get_short_sha(ctx.repo_dir)
    us = f"gs://matterlabs-setup-data-us/{short_sha}-gpu/"
    asia = f"gs://matterlabs-setup-data-asia/{short_sha}-gpu/"
    europe = f"gs://matterlabs-setup-data-europe/{short_sha}-gpu/"
    with ctx.section("Generate json for paths", expected=5):
        json_content = f"""
        {{
            "sha": "{short_sha}-gpu",
            "us": "{us}",
            "europe": "{europe}",
            "asia": "{asia}"
        }}
        """
        setup_data_gpu_keys_json.write_text(json_content.strip())

    with ctx.section("Upload data to GCP", expected=300):
        ctx.sh(f"gsutil -m rsync ./prover/data/keys {us}")

    with ctx.section("Replicate US -> asia/europe", expected=300):
        ctx.sh(f"gsutil -m rsync -r {us} {asia}")
        ctx.sh(f"gsutil -m rsync -r {us} {europe}")


if __name__ == "__main__":
    run_script(script)
