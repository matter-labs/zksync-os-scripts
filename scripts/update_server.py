#!/usr/bin/env python3
"""
Python port of the zkos L1 state/genesis setup script.

Steps:
- Check env + tooling
- Build zkstack CLI
- Build era-contracts L1, generate genesis.json
- Initialize ecosystem (zksync-os mode)
- Start Anvil, fund accounts, deploy L1 contracts
- Extract bridgehub + operator keys, update config_constants.rs
- Generate deposit tx
- Stop Anvil
"""

from pathlib import Path

from lib.ctx import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib import edit_server
from lib import constants


# ---------------------------------------------------------------------------
# Funding logic
# ---------------------------------------------------------------------------
def fund_accounts(ctx: ScriptCtx, ecosystem_dir: Path) -> None:
    """
    Approximate port of the bash funding logic:
    - Find all wallets.yaml
    - For each, extract addresses and send 10 ETH
    - Then two large transfers between rich wallets
    """

    if not ecosystem_dir.is_dir():
        ctx.fail(f"Ecosystem dir not found: {ecosystem_dir}")

    wallets_files = list(ecosystem_dir.rglob("wallets.yaml"))
    if not wallets_files:
        ctx.fail(f"No wallets.yaml found under {ecosystem_dir}")

    all_addrs: set[str] = set()
    for wf in wallets_files:
        data = utils.load_yaml(wf)
        addrs = utils.addresses_from_wallets_yaml(data)
        if addrs:
            ctx.logger.info(f"Found {len(addrs)} addresses in {wf}")
            all_addrs.update(addrs)

    rpc_url: str = constants.ANVIL_DEFAULT_URL

    # Fund each address
    ctx.logger.info(f"Funding {len(all_addrs)} addresses with 10 ETH each...")
    for addr in sorted(all_addrs):
        ctx.sh(
            f"""
            cast send {addr}
              --value 10ether
              --private-key {constants.ANVIL_RICH_PRIVATE_KEY}
              --rpc-url {rpc_url}
            """
        )

    # Two large transfers between rich wallets
    ctx.sh(
        f"""
        cast send 0xa61464658afeaf65cccaafd3a512b69a83b77618
          --value 9000ether
          --private-key 0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6
          --rpc-url {rpc_url}
        """
    )
    ctx.sh(
        f"""
        cast send 0x36615cf349d7f6344891b1e7ca7c72883f5dc049
          --value 9000ether
          --private-key 0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97
          --rpc-url {rpc_url}
        """
    )


def init_ecosystem(
    ctx: ScriptCtx,
    ecosystem_name: str,
    chains: list[str],
) -> None:
    era_contracts_path = ctx.path_from_env("ERA_CONTRACTS_PATH", "era-contracts")
    zksync_era_path = ctx.path_from_env("ZKSYNC_ERA_PATH", "zksync-era")
    protocol_version = utils.require_env("PROTOCOL_VERSION")

    zkstack_bin = zksync_era_path / "zkstack_cli" / "target" / "release" / "zkstack"
    ecosystems_dir = ctx.tmp_dir / "ecosystems"
    ecosystem_dir = ctx.tmp_dir / "ecosystems" / ecosystem_name

    with ctx.section(f"Initialize {ecosystem_name} ecosystem", expected=120):
        utils.clean_dir(ecosystem_dir)
        ctx.sh(
            f"""
                {zkstack_bin}
                  ecosystem create
                  --ecosystem-name {ecosystem_name}
                  --l1-network localhost
                  --chain-name tmp-chain
                  --chain-id 12345
                  --prover-mode no-proofs
                  --wallet-creation random
                  --link-to-code {zksync_era_path}
                  --l1-batch-commit-data-generator-mode rollup
                  --start-containers false
                  --base-token-address 0x0000000000000000000000000000000000000001
                  --base-token-price-nominator 1
                  --base-token-price-denominator 1
                  --evm-emulator false
                """,
            cwd=ecosystems_dir,
        )
        ctx.sh(
            f"""
                {zkstack_bin}
                  ctm set-ctm-contracts
                  --contracts-src-path {era_contracts_path}
                  --default-configs-src-path {ctx.repo_dir / "local-chains" / protocol_version / "default"}
                  --zksync-os
                """,
            cwd=ecosystem_dir,
        )
        # Remove default era chain (non zksync-os)
        utils.clean_dir(ecosystem_dir / "chains")

    for chain in chains:
        ctx.sh(
            f"""
            {zkstack_bin}
              chain create
              --chain-name {chain}
              --chain-id {chain}
              --prover-mode no-proofs
              --wallet-creation random
              --l1-batch-commit-data-generator-mode rollup
              --base-token-address 0x0000000000000000000000000000000000000001
              --base-token-price-nominator 1
              --base-token-price-denominator 1
              --evm-emulator false
              --set-as-default=true
              --zksync-os
            """,
            cwd=ecosystem_dir,
        )

        # ------------------------------------------------------------------ #
        # Start Anvil
        # ------------------------------------------------------------------ #
        with ctx.section(
            f"Generating zkos-l1-state.json for {ecosystem_name}", expected=250
        ):
            base = ctx.repo_dir / "local-chains" / protocol_version / ecosystem_name
            l1_state_file = base / "zkos-l1-state.json"
            with utils.anvil_dump_state(l1_state_file=l1_state_file):
                # ------------------------------------------------------------------ #
                # Fund accounts
                # ------------------------------------------------------------------ #
                ctx.logger.info("Funding accounts...")
                fund_accounts(ctx, ecosystem_dir)
                # ------------------------------------------------------------------ #
                # Deploy L1 contracts via zkstack
                # ------------------------------------------------------------------ #
                ctx.logger.info("Deploying L1 contracts...")
                ctx.sh(
                    f"""
                        {zkstack_bin}
                          ecosystem init
                          --deploy-paymaster=false
                          --deploy-erc20=false
                          --observability=false
                          --no-port-reallocation
                          --deploy-ecosystem
                          --l1-rpc-url="{constants.ANVIL_DEFAULT_URL}"
                          --zksync-os
                        """,
                    cwd=ecosystem_dir,
                )

                # ------------------------------------------------------------------ #
                # Update contract addresses and operator keys
                # ------------------------------------------------------------------ #
                ctx.logger.info("Updating contract addresses...")
                contracts_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "contracts.yaml"
                )
                chain_wallets_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
                )
                chain_config_yaml = (
                    base / f"chain_{chain}.yaml"
                    if ecosystem_name == "multi_chain"
                    else base / "config.yaml"
                )
                edit_server.update_chain_config_yaml(
                    chain_config_yaml,
                    contracts_yaml=contracts_yaml,
                    wallets_yaml=chain_wallets_yaml,
                )
                if ecosystem_name == "multi_chain":
                    wallets_out = base / f"wallets_{chain}.yaml"
                    contracts_out = base / f"contracts_{chain}.yaml"
                else:
                    wallets_out = base / "wallets.yaml"
                    contracts_out = base / "contracts.yaml"
                utils.cp(chain_wallets_yaml, wallets_out)
                utils.cp(contracts_yaml, contracts_out)
                # ------------------------------------------------------------------ #
                # Generate deposit transaction
                # ------------------------------------------------------------------ #
                ctx.logger.info("Generating L1 -> L2 deposit transaction...")
                bridgehub_address = edit_server.get_contract_address(
                    contracts_yaml,
                    "bridgehub_proxy_addr",
                )
                ctx.sh(
                    f"""
                    cargo run --release --package zksync_os_generate_deposit -- --bridgehub "{bridgehub_address}" --chain-id {chain}
                    """
                )
                if chain == constants.GATEWAY_CHAIN_ID:
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway create-tx-filterer
                            --chain {constants.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{constants.ANVIL_DEFAULT_URL}"
                            --ignore-prerequisites
                            """,
                        cwd=ecosystem_dir,
                    )
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway convert-to-gateway
                            --chain {constants.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{constants.ANVIL_DEFAULT_URL}"
                            --ignore-prerequisites
                            --no-gateway-overrides
                            """,
                        cwd=ecosystem_dir,
                    )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def script(ctx: ScriptCtx) -> None:
    # Paths & constants
    era_contracts_path = ctx.path_from_env("ERA_CONTRACTS_PATH", "era-contracts")
    zksync_era_path = ctx.path_from_env("ZKSYNC_ERA_PATH", "zksync-era")
    zksync_os_execution_version = utils.require_env("ZKSYNC_OS_EXECUTION_VERSION")
    proving_version = utils.require_env("PROVING_VERSION")
    protocol_version = utils.require_env("PROTOCOL_VERSION")

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    ctx.require_cmds(
        {
            "yarn": ">=1.22",
            "anvil": "==1.5.1",
            "cast": "==1.3.5",
            "forge": "==1.3.5",
            "cargo": ">=1.89",
        }
    )

    # ------------------------------------------------------------------ #
    # Build L1 contracts
    # ------------------------------------------------------------------ #
    with ctx.section("Build L1 contracts", expected=120):
        ctx.sh(
            """
            yarn build:foundry
            """,
            cwd=era_contracts_path / "l1-contracts",
        )

    # ------------------------------------------------------------------ #
    # Build zkstack CLI
    # ------------------------------------------------------------------ #
    with ctx.section("Build zkstack CLI", expected=100):
        ctx.sh(
            """
            cargo build --release --bin zkstack
            """,
            cwd=zksync_era_path / "zkstack_cli",
        )

    # ------------------------------------------------------------------ #
    # Generate genesis.json
    # ------------------------------------------------------------------ #
    with ctx.section("Generate genesis.json", expected=60):
        ctx.sh(
            f"""
            cargo run --
              --output-file {ctx.repo_dir / "local-chains" / protocol_version / "default" / "genesis.json"}
              --execution-version {zksync_os_execution_version}
            """,
            cwd=era_contracts_path / "tools" / "zksync-os-genesis-gen",
        )

    # ------------------------------------------------------------------ #
    # Single-chain setup
    # ------------------------------------------------------------------ #
    init_ecosystem(ctx, "default", ["6565"])

    # ------------------------------------------------------------------ #
    # Multi-chain setup
    # ------------------------------------------------------------------ #
    if protocol_version == "v30.2":
        init_ecosystem(ctx, "multi_chain", ["6565", "6566"])
    else:
        init_ecosystem(ctx, "multi_chain", ["6565", "6566", constants.GATEWAY_CHAIN_ID])

    # ------------------------------------------------------------------ #
    # Update VK hash in prover config
    # ------------------------------------------------------------------ #
    edit_server.update_vk_hash(
        ctx.repo_dir / "lib" / "types" / "src" / "protocol" / "proving_version.rs",
        proving_version,
    )

    # ------------------------------------------------------------------ #
    # Regenerate contracts.json
    # ------------------------------------------------------------------ #
    with ctx.section("Regenerate contracts.json", expected=30):
        ctx.sh("yarn install", cwd=era_contracts_path / "l1-contracts")
        ctx.sh(
            f"""
            yarn write-factory-deps-zksync-os
            --output {ctx.repo_dir}/lib/l1_watcher/src/factory_deps/contracts.json
            """,
            cwd=era_contracts_path / "l1-contracts",
        )


if __name__ == "__main__":
    run_script(
        script,
        required_env=(
            "ERA_CONTRACTS_PATH",
            "ZKSYNC_ERA_PATH",
            "ZKSYNC_OS_EXECUTION_VERSION",
            "PROVING_VERSION",
            "PROTOCOL_VERSION",
        ),
    )
