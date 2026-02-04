# Prerequisites

For running the scripts, you will need the following tools installed on your system.

## System requirements

- Operating System: Unix-like (Linux, macOS).
- Disk Space: At least 10 GB of free disk space to accommodate the repositories, dependencies, and generated files.
- Memory: Minimum 32 GB of RAM for smooth operation.
- CPU: modern multi-core processor to handle the computational tasks efficiently.

## Required tools

- **uv** - Python dependency and environment manager<br>
  👉 [Installation guide](https://docs.astral.sh/uv/getting-started/installation/)

- **Node.js ≥ v22.x** - JavaScript runtime for building contracts<br>
  👉 [Installation guide (Node + npm)](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm)

- **yarn ≥ 1.22** - Package manager used by the contracts workspace<br>
  👉 [Installation guide](https://yarnpkg.com/getting-started/install)

- **Rust and Cargo** - Required for building tools and ZKsync OS components<br>
  👉 [Installation guide](https://www.rust-lang.org/tools/install)

- **Foundry == 1.5.1** - Anvil<br>
  👉 [Installation guide](https://book.getfoundry.sh/getting-started/installation)<br>
  `foundryup -i 1.5.1`

### For protocol version v30.2

- **Foundry-zksync == nightly-ae913af65381734ad46c044a9495b67310bc77c4**<br>
  👉 [Download binaries from GitHub release](https://github.com/matter-labs/foundry-zksync/releases/tag/nightly-ae913af65381734ad46c044a9495b67310bc77c4)

### For protocol version v31.0
- **Foundry-zksync == 1.3.5-foundry-zksync-v0.1.5**<br>
  👉 [Installation guide](https://foundry-book.zksync.io/)<br>
  `foundryup-zksync -i 0.1.5`

## Quick install (recommended)

If you don’t already have the required tools installed, the following commands will get you started on most Unix-like systems (macOS, Linux):

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Node.js via nvm (recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash
nvm install 22

# Install yarn
npm install -g yarn

# Install Rust (rustup)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install Foundry
curl -L https://foundry.paradigm.xyz | bash
foundryup -i 1.5.1

# (FOR v30.2 ONLY): download foundry-zksync binaries (macOS ARM64 example, adjust for your OS/arch)
ZKSYNC_FOUNDRY_URL="https://github.com/matter-labs/foundry-zksync/releases/download"
ZKSYNC_FOUNDRY_VERSION="nightly-ae913af65381734ad46c044a9495b67310bc77c4"
curl -L ${ZKSYNC_FOUNDRY_URL}/${ZKSYNC_FOUNDRY_VERSION}/foundry_nightly_darwin_arm64.tar.gz | tar xz -C ${HOME}/.foundry/bin

# (FOR v31.0 ONLY): install foundry-zksync through foundryup-zksync
curl -L https://raw.githubusercontent.com/matter-labs/foundry-zksync/main/install-foundry-zksync | bash
foundryup-zksync -i 0.1.5
```

## Init Python environment

The project uses [`uv`](https://docs.astral.sh/uv/) to manage Python versions and dependencies.

~~~admonish example title="Initialize python environment"
```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the required Python version
uv python install

# Sync dependencies
uv sync
```
~~~

## Verify installation

You can verify that all required tools are installed correctly:

```bash
uv --version
node --version
yarn --version
cargo --version
cast --version
forge --version
anvil --version
```

The output should look similar to this (example for protocol version `v31.0`):

```bash
# uv version
uv 0.9.25 (38fcac0f3 2026-01-13)
# node version
v22.20.0
# yarn version
1.22.22
# rust and cargo versions
cargo 1.89.0 (c24e10642 2025-06-23)
# cast and forge versions
cast Version: 1.3.5-foundry-zksync-v0.1.5
forge Version: 1.3.5-foundry-zksync-v0.1.5
# anvil version
anvil Version: 1.5.1-v1.5.1
```
