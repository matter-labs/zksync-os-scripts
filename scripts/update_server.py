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
from packaging.version import Version

from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib import edit_server
from lib import config
from lib.protocol_version import (
    PROTOCOL_TOOLCHAINS,
    PROTOCOL_VERSION_CURRENT,
    PROTOCOL_VERSION_NEXT,
)


# ---------------------------------------------------------------------------
# Funding logic
# ---------------------------------------------------------------------------
def fund_accounts(ctx: ScriptCtx, ecosystem_dir: Path) -> None:
    """
    Approximate port of the bash funding logic:
    - Find all wallets.yaml
    - For each, extract addresses and send 100 ETH
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
            ctx.logger.info(f"Found {addrs} addresses in {wf}")
            all_addrs.update(addrs)

    rpc_url: str = config.ANVIL_DEFAULT_URL

    # Fund each address
    ctx.logger.info(f"Funding {len(all_addrs)} addresses with 100 ETH each...")
    amount_100eth = hex(100 * 10 ** 18)
    for addr in sorted(all_addrs):
        ctx.sh(
            f"""cast rpc anvil_setBalance {addr} {amount_100eth} --rpc-url {rpc_url}""",
            print_command=False,
        )

    # Two large transfers between rich wallets
    ctx.logger.debug("Performing two large transfers between rich wallets...")
    amount_9000eth = hex(9000 * 10 ** 18)
    ctx.sh(
        f"""
        cast rpc anvil_setBalance 0xa61464658afeaf65cccaafd3a512b69a83b77618 {amount_9000eth} --rpc-url {rpc_url}
        """,
        print_command=False,
    )
    ctx.sh(
        f"""
        cast rpc anvil_setBalance 0x36615cf349d7f6344891b1e7ca7c72883f5dc049 {amount_9000eth} --rpc-url {rpc_url}
        """,
        print_command=False,
    )


def init_ecosystem(
    ctx: ScriptCtx,
    ecosystem_name: str,
    chains: list[str],
) -> None:
    era_contracts_path = utils.require_path("ERA_CONTRACTS_PATH")
    zksync_era_path = utils.require_path("ZKSYNC_ERA_PATH")
    protocol_version = utils.require_env("PROTOCOL_VERSION")

    zkstack_bin = zksync_era_path / "zkstack_cli" / "target" / "release" / "zkstack"
    ecosystems_dir = ctx.workspace / "ecosystems"
    ecosystem_dir = ctx.workspace / "ecosystems" / ecosystem_name
    protocol_base = ctx.repo_dir / "local-chains" / protocol_version
    gateway_db = ctx.workspace / "gateway-db"
    default_base = protocol_base / "default"
    gateway_base = protocol_base / "gateway"
    base = protocol_base / ecosystem_name

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
            if chain == config.GATEWAY_CHAIN_ID:
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
        f"Generating l1-state.json.gz for {ecosystem_name}", expected=250
    ):
        l1_state_file = protocol_base / "l1-state.json.gz"
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
                      --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                      --zksync-os
                    """,
                cwd=ecosystem_dir,
            )
            for chain in chains:
                if chain != config.GATEWAY_CHAIN_ID:
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
                              --set-as-default=false
                              --zksync-os
                        """,
                        cwd=ecosystem_dir,
                    )
                    ctx.logger.info(f"Funding accounts for chain {chain}...")
                    fund_accounts(ctx, ecosystem_dir)
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                              chain init
                              --chain {chain}
                              --deploy-paymaster=false
                              --no-port-reallocation
                              --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                              --skip-priority-txs
                              --pause-deposits
                        """,
                        cwd=ecosystem_dir,
                    )
            chain_operator_sks = []
            for chain in chains:
                if chain == config.GATEWAY_CHAIN_ID:
                    continue
                wallets_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
                )
                wallets = utils.load_yaml(wallets_yaml)
                for role in ["operator", "prove_operator", "execute_operator"]:
                    entry = wallets.get(role)
                    if not isinstance(entry, dict) or not entry.get("private_key"):
                        raise SystemExit(
                            f"Missing private_key for '{role}' in {wallets_yaml}"
                        )
                    sk_raw = entry["private_key"]
                    sk = utils.normalize_hex(sk_raw, length=64)
                    chain_operator_sks.append(sk)
            ctx.logger.info(f"Gateway chain operators: {chain_operator_sks}")

            for chain in chains:
                # ------------------------------------------------------------------ #
                # Update contract addresses and operator keys
                # ------------------------------------------------------------------ #
                ctx.logger.debug("Updating contract addresses...")
                contracts_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "contracts.yaml"
                )
                chain_wallets_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
                )
                edit_server.update_chain_config_yaml(
                    base / f"chain_{chain}.yaml",
                    contracts_yaml=contracts_yaml,
                    wallets_yaml=chain_wallets_yaml,
                )
                # If this is the first chain we also use it to update `default` config
                if chain == "6565":
                    edit_server.update_chain_config_yaml(
                        default_base / "config.yaml",
                        contracts_yaml=contracts_yaml,
                        wallets_yaml=chain_wallets_yaml,
                    )
                name_suffix = f"_{chain}" if ecosystem_name == "multi_chain" else ""
                wallets_out = base / f"wallets{name_suffix}.yaml"
                contracts_out = base / f"contracts{name_suffix}.yaml"
                # Copy wallets.yaml and contracts.yaml to local-chains
                utils.cp(chain_wallets_yaml, wallets_out)
                utils.cp(contracts_yaml, contracts_out)
                if chain == config.GATEWAY_CHAIN_ID:
                    edit_server.update_chain_config_yaml(
                        gateway_base / "config.yaml",
                        contracts_yaml=contracts_yaml,
                        wallets_yaml=chain_wallets_yaml,
                    )
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
                        cargo run --release --package zksync_os_generate_deposit -- --bridgehub "{bridgehub_address}" --chain-id {chain} --amount 100
                        """
                    )
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway create-tx-filterer
                            --chain {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --ignore-prerequisites
                            """,
                        cwd=ecosystem_dir,
                    )
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway convert-to-gateway
                            --chain {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --ignore-prerequisites
                            --no-gateway-overrides
                            """,
                        cwd=ecosystem_dir,
                    )
                    ctx.logger.info("Generating L1 -> L2 gateway deposit transactions for chain operators...")
                    for chain_operator_sk in chain_operator_sks:
                        ctx.sh(
                            f"""
                            cargo run --release --package zksync_os_generate_deposit --
                            --bridgehub "{bridgehub_address}"
                            --chain-id {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --private-key {chain_operator_sk}
                            --amount 10.0
                            """
                        )
                else:
                    ctx.logger.info("TBD")
                    # ctx.sh(
                    #     f"""
                    #         {zkstack_bin}
                    #         chain pause-deposits
                    #         --chain {chain}
                    #         --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                    #         """,
                    #     cwd=ecosystem_dir,
                    # )
            ctx.sh(f"cargo build --release", cwd=ctx.repo_dir)
            utils.clean_dir(gateway_db)
            utils.remove_dir(ctx.workspace / "gateway-state.tar.gz")
            with utils.gateway(repo_path=ctx.repo_dir, db_path=gateway_db):
                for chain in chains:
                    if chain == config.GATEWAY_CHAIN_ID:
                        continue
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway migrate-to-gateway
                            --chain {chain}
                            --gateway-chain-name {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --gateway-rpc-url http://localhost:3052
                        """,
                        cwd=ecosystem_dir,
                        verbose=True,
                    )
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway finalize-chain-migration-to-gateway
                            --chain {chain}
                            --gateway-chain-name {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --gateway-rpc-url http://localhost:3052
                            --deploy-paymaster=false
                        """,
                        cwd=ecosystem_dir,
                        verbose=True,
                    )
            ctx.sh(f"tar czvf gateway-state.tar.gz -C ./gateway-db .", cwd=ctx.workspace)
            utils.cp(ctx.workspace / "gateway-state.tar.gz", protocol_base / "gateway-state.tar.gz")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def script(ctx: ScriptCtx) -> None:
    # Paths & constants
    era_contracts_path: Path = utils.require_path("ERA_CONTRACTS_PATH")
    zksync_era_path: Path = utils.require_path("ZKSYNC_ERA_PATH")
    protocol_version: str = utils.require_env("PROTOCOL_VERSION")
    try:
        toolchain = PROTOCOL_TOOLCHAINS[protocol_version]
    except KeyError:
        raise ValueError(
            f"Unsupported PROTOCOL_VERSION: {protocol_version}. Supported: {list(PROTOCOL_TOOLCHAINS.keys())}"
        )
    execution_version: str = toolchain.execution_version
    proving_version: str = toolchain.proving_version
    cast_forge_version: str = toolchain.cast_forge_version
    anvil_version: str = toolchain.anvil_version
    cargo_version: str = toolchain.cargo_version
    yarn_version: str = toolchain.yarn_version

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "yarn": f">={yarn_version}",
            "anvil": f"=={anvil_version}",
            "cast": f"=={cast_forge_version}",
            "forge": f"=={cast_forge_version}",
            "cargo": f">={cargo_version}",
        }
    )

    # TODO: remove this later, needs only for v31 for now
    # ------------------------------------------------------------------ #
    # Build contracts for zkstack (temporary)
    # ------------------------------------------------------------------ #
    if Version(protocol_version) >= Version(PROTOCOL_VERSION_NEXT):
        zkstack_era_contracts_path: Path = zksync_era_path / "contracts"
        with ctx.section("Build contracts in zkstack", expected=120):
            ctx.sh(
                """
                yarn install
                """,
                cwd=zkstack_era_contracts_path,
            )
            ctx.sh(
                """
                yarn build:foundry
                """,
                cwd=zkstack_era_contracts_path / "da-contracts",
            )
            ctx.sh(
                """
                yarn build:foundry
                """,
                cwd=zkstack_era_contracts_path / "l1-contracts",
            )

    # ------------------------------------------------------------------ #
    # Build contracts
    # ------------------------------------------------------------------ #
    with ctx.section("Build contracts", expected=120):
        ctx.sh(
            """
            yarn install
            """,
            cwd=era_contracts_path,
        )
        ctx.sh(
            """
            yarn build:foundry
            """,
            cwd=era_contracts_path / "da-contracts",
        )
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
              --output-file {ctx.repo_dir / "local-chains" / protocol_version / "genesis.json"}
              --execution-version {execution_version}
            """,
            cwd=era_contracts_path / "tools" / "zksync-os-genesis-gen",
        )

    # # ------------------------------------------------------------------ #
    # # Single-chain setup
    # # ------------------------------------------------------------------ #
    # init_ecosystem(ctx, "default", ["6565"])

    # ------------------------------------------------------------------ #
    # Multi-chain setup
    # ------------------------------------------------------------------ #
    if Version(protocol_version) == Version(PROTOCOL_VERSION_CURRENT):
        init_ecosystem(ctx, "multi_chain", ["6565", "6566"])

    if Version(protocol_version) >= Version(PROTOCOL_VERSION_NEXT):
        init_ecosystem(ctx, "multi_chain", ["6565", "6566", config.GATEWAY_CHAIN_ID])

    # ------------------------------------------------------------------ #
    # Update VK hash in prover config
    # ------------------------------------------------------------------ #
    edit_server.update_vk_hash(
        ctx.repo_dir / "lib" / "types" / "src" / "protocol" / "proving_version.rs",
        era_contracts_path
        / "l1-contracts"
        / "contracts"
        / "state-transition"
        / "verifiers"
        / "ZKsyncOSVerifierPlonk.sol",
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
            "PROTOCOL_VERSION",
        ),
    )
