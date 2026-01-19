# zksync-os-scripts

Operational scripts for ZKsync OS protocol upgrades.

This repository contains a collection of Python utilities used to safely and reproducibly perform ZKsync OS upgrades, including:
* Updating verification keys
* Updating genesis and L1 state
* Updating prover configuration and artifacts
* Coordinating server-side protocol changes

The scripts are written in shell-like style, and are designed to be explicit, easy to contribute, and automation-friendly.

## Why Python?

Python provides a good balance between readability, ease of use, and powerful libraries for scripting and automation.
It allows us to write clear and maintainable code that can be easily understood by team members.

Shell scripts can become complex and hard to manage as the project grows, while Python offers better structure and error handling.

In addition, Python does not require third-party dependencies for most common tasks,
making it easier to set up and run the scripts in various environments.

## Requirements

- **Python 3.12+**
- **[uv](https://github.com/astral-sh/uv)** for dependency management and execution

## Getting started

The project uses `uv` to manage Python versions and dependencies:
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# Install python
uv python install
# Sync dependencies
uv sync
```

## Usage

All scripts are executed via `uv` to ensure a fully reproducible environment.

In order to run a specific script, use the following command pattern:
```bash
uv run -m scripts.<script_name>
```

## Env variables

The scripts are designed to be configurable via environment variables.

Common environment variables used across all scripts include:
- `REPO_DIR` - path to the ZKsync OS repository that should be modified by the script.
- `WORKSPACE` - path to a temporary working directory that the script can use for intermediate files. Defaults to `${PWD}`.
- `VERBOSE` - if set to `1` or `true`, enables verbose logging for debugging purposes.

## Available scripts

### `update_wrapper.py`

The script updates `zkos-wrapper` repository
with the new SNARK proof to prepare verification key generation on the next step using `update_vk.py` script.

Usage:
```bash
REPO_DIR=/path/to/zkos-wrapper \
ZKSYNC_AIRBENDER_PATH=/path/to/zksync-airbender \
    uv run -m scripts.update_wrapper
```

### `update_vk.py`

Generates new verification keys.

- Downloads the trusted setup (CRS) file
- Downloads the ZKsync OS binary for a specified release tag
- Generates a new SNARK verification key
- Uses zkos-wrapper to derive the verification key from the OS binary and trusted setup
- Regenerates verifier smart contracts
- Feeds the new verification key into the verifier contract generator
- Produces updated Solidity verifier contracts for ZKsync OS
- Recomputes hashes and fixtures affected by the verifier changes

Usage:
```bash
REPO_DIR=/path/to/era-contracts \
ZKOS_WRAPPER_PATH=/path/to/zkos-wrapper \
ZKSYNC_OS_TAG=<new_zksync_os_tag> \
    uv run -m scripts.update_vk
```

### `update_server.py`

Updates ZKsync OS Server with the new genesis and state.

- Build zkstack CLI: Compiles the zkstack command used to create/init ecosystems and chains.
- Build L1 contracts: Builds L1 contracts.
- Generate genesis.json.
- Create default (single-chain) local setup.
- Create multi-chain local setup.
- Fund local wallets.
- Deploy L1 contracts.
- Extract and write config outputs.
- Generate L1 -> L2 deposit tx.
- Update prover config VK hash.
- Regenerate `contracts.json` used by the L1 watcher.

Usage:
```bash
REPO_DIR=/path/to/zksync-os-server \
ERA_CONTRACTS_PATH=/path/to/era-contracts \
ZKSYNC_ERA_PATH=/path/to/zksync-era \
ZKSYNC_OS_EXECUTION_VERSION=<execution_version> \
PROVING_VERSION=<proving_version> \
PROTOCOL_VERSION=<protocol_version> \
    uv run -m scripts.update_server
```

### `update_prover.py`

Updates ZKsync Airbender Prover with a new ZKsync OS binary.

- Replaces ZKsync OS binary used by the prover with a new version corresponding to the specified release tag.
- Updates prover configuration to reference the new binary and its associated parameters.

Usage:
```bash
REPO_DIR=/path/to/zksync-airbender-prover \
ZKSYNC_OS_TAG=<new_zksync_os_tag> \
    uv run -m scripts.update_prover
```
