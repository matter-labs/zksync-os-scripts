#!/usr/bin/env python3
"""
Local state update script for zksync-os-server.

Steps:
- Check env + tooling
- Build zkstack CLI
- Build era-contracts L1, generate genesis.json
- Initialize ecosystem (zksync-os mode)
- Start Anvil, fund accounts, deploy L1 contracts
- Extract bridgehub + operator keys
- Generate L1 -> L2 deposit tx
- Stop Anvil and dump the new zkos-l1-state.json
"""

import yaml
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    - Then fund two (hardcoded) rich wallets with 9000 ETH each
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
            ctx.logger.debug(f"Found {addrs} addresses in {wf}")
            all_addrs.update(addrs)

    rpc_url: str = config.ANVIL_DEFAULT_URL

    # Fund each address
    ctx.logger.debug(f"Funding {len(all_addrs)} addresses with 100 ETH each...")
    amount_100eth = hex(100 * 10**18)
    for addr in sorted(all_addrs):
        ctx.sh(
            f"cast rpc anvil_setBalance {addr} {amount_100eth} --rpc-url {rpc_url}",
            print_command=False,
        )

    # Two large transfers between rich wallets
    ctx.logger.debug("Funding two rich wallets with 9000 ETH each...")
    amount_9000eth = hex(9000 * 10**18)
    ctx.sh(
        f"cast rpc anvil_setBalance 0xa61464658afeaf65cccaafd3a512b69a83b77618 {amount_9000eth} --rpc-url {rpc_url}",
        print_command=False,
    )
    ctx.sh(
        f"cast rpc anvil_setBalance 0x36615cf349d7f6344891b1e7ca7c72883f5dc049 {amount_9000eth} --rpc-url {rpc_url}",
        print_command=False,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _create_and_init_chain(
    ctx: ScriptCtx,
    zkstack_bin: Path,
    ecosystem_dir: Path,
    chain: str,
    *,
    pause_deposits: bool = True,
) -> None:
    """Create, fund, and initialise a single chain on L1."""
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
    ctx.logger.debug(f"Funding accounts for chain {chain}...")
    fund_accounts(ctx, ecosystem_dir)
    init_args = " --skip-priority-txs --pause-deposits" if pause_deposits else ""
    ctx.sh(
        f"""
            {zkstack_bin}
              chain init
              --chain {chain}
              --deploy-paymaster=false
              --no-port-reallocation
              --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"{init_args}
        """,
        cwd=ecosystem_dir,
    )


def _collect_operator_sks(ecosystem_dir: Path, chains: list[str]) -> list[str]:
    """Collect operator private keys (operator, prove_operator, execute_operator) for each chain."""
    sks: list[str] = []
    for chain in chains:
        wallets_yaml = ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
        wallets = utils.load_yaml(wallets_yaml)
        for role in ["operator", "prove_operator", "execute_operator"]:
            entry = wallets.get(role)
            if not isinstance(entry, dict) or not entry.get("private_key"):
                raise SystemExit(f"Missing private_key for '{role}' in {wallets_yaml}")
            sks.append(utils.normalize_hex(entry["private_key"], length=64))
    return sks


# ---------------------------------------------------------------------------
# Chain config helpers
# ---------------------------------------------------------------------------
def _write_chain_base_config(
    yaml_path: Path,
    chain_id: str,
    protocol_version: str,
    *,
    gateway_rpc_url: str | None = None,
    ephemeral: bool | None = None,
    ephemeral_state: str | None = None,
    rpc_port: int | None = None,
) -> None:
    """
    Create a base chain config YAML.

    When *gateway_rpc_url* is provided the config targets a gateway-settling
    chain (adds ``general``, ``l1_sender.pubdata_mode``, and ``rpc`` sections).
    Otherwise it produces an L1-settling chain config.

    Dynamic fields (contract addresses, operator keys) are filled in
    afterwards by ``edit_server.update_chain_config_yaml``.
    """
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}

    general: dict = {}
    if gateway_rpc_url is not None:
        general["gateway_rpc_url"] = gateway_rpc_url
    if ephemeral is not None:
        general["ephemeral"] = ephemeral
    if ephemeral_state is not None:
        general["ephemeral_state"] = ephemeral_state
    data["general"] = general

    data["genesis"] = {
        "bridgehub_address": "",
        "bytecode_supplier_address": "",
        "genesis_input_path": f"./local-chains/{protocol_version}/genesis.json",
        "chain_id": int(chain_id),
    }

    l1_sender: dict = {}
    if gateway_rpc_url is not None:
        l1_sender["pubdata_mode"] = "RelayedL2Calldata"
    else:
        l1_sender["pubdata_mode"] = "Blobs"
    l1_sender.update({
        "operator_commit_sk": "",
        "operator_prove_sk": "",
        "operator_execute_sk": "",
    })
    data["l1_sender"] = l1_sender

    if rpc_port is not None:
        data["rpc"] = {"address": f"0.0.0.0:{rpc_port}"}

    data["external_price_api_client"] = {
        "source": "Forced",
        "forced_prices": {
            "0x0000000000000000000000000000000000000001": 3000,
        },
    }

    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Ecosystem setup strategies
# ---------------------------------------------------------------------------
@dataclass
class EcosystemSetup(ABC):
    """
    Strategy that captures the differences between gateway and no-gateway setups.
    Subclasses override hooks; init_ecosystem stays linear with no conditionals.
    """

    ecosystem_name: str
    user_chains: list[str]  # chains excluding any gateway chain

    @property
    @abstractmethod
    def initial_chain(self) -> str:
        """
        The chain created before `ecosystem init` and set as the zkstack default.
        `ecosystem init` deploys and initialises this chain; all other user chains
        are created and initialised afterwards.
        """
        ...

    @property
    def all_chains(self) -> list[str]:
        """All chains whose configs must be written (may include a gateway chain)."""
        return list(self.user_chains)

    def chain_output_paths(
        self, chain: str, protocol_base: Path
    ) -> tuple[Path, Path, Path]:
        """Return (config_yaml, wallets_dst, contracts_dst) for a chain."""
        d = protocol_base / self.ecosystem_name / chain
        return (
            d / "config.yaml",
            d / "wallets.yaml",
            d / "contracts.yaml",
        )

    def use_blob_operator_for(self, chain: str) -> bool:
        """Return True if chain config should use the blob operator key."""
        return False

    def on_initial_chain_ready(
        self,
        ctx: ScriptCtx,
        ecosystem_dir: Path,
        zkstack_bin: Path,
        contracts_yaml: Path,
        wallets_yaml: Path,
        chain_operator_sks: list[str],
        protocol_base: Path,
    ) -> None:
        """
        Hook called (inside the Anvil session) right after the initial chain's
        config files have been written.  Gateway setups override this to perform
        gateway-specific L1 operations (deposit tx, convert-to-gateway, …).
        """

    def run_gateway_phase(
        self,
        ctx: ScriptCtx,
        zkstack_bin: Path,
        ecosystem_dir: Path,
        protocol_version: str,
        protocol_base: Path,
    ) -> None:
        """
        Hook called (still inside the Anvil session) after all chain configs have
        been written.  Gateway setups override this to build the server, start the
        gateway, migrate chains, and archive the gateway DB.
        """

    def ensure_chain_base_config(
        self, yaml_path: Path, chain: str, protocol_version: str
    ) -> None:
        """Create base chain config YAML if it doesn't already exist."""
        if yaml_path.exists():
            return
        _write_chain_base_config(yaml_path, chain, protocol_version)

    def get_l1_settling_chains(self) -> list[str]:
        """Return chain IDs that settle directly on L1 (not via gateway)."""
        return []

    def write_l1_settling_configs(
        self,
        ctx: ScriptCtx,
        zkstack_bin: Path,
        ecosystem_dir: Path,
        protocol_version: str,
        protocol_base: Path,
    ) -> None:
        """
        Hook to write L1-settling chain configs to the default/ directory.
        Override in setups that produce configs for chains settling directly on L1.
        """


@dataclass
class NoGatewaySetup(EcosystemSetup):
    """
    Ecosystem without a gateway chain.
    Used for v30.2, and optionally for v31.0 when USE_GATEWAY=false.
    """

    @property
    def initial_chain(self) -> str:
        return self.user_chains[0]


@dataclass
class GatewaySetup(EcosystemSetup):
    """
    Ecosystem with a gateway chain.
    Used for v31.0 when USE_GATEWAY=true (the default).
    """

    gateway_chain_id: str
    l1_settling_chains: list[str] = field(default_factory=list)

    @property
    def initial_chain(self) -> str:
        return self.gateway_chain_id

    @property
    def all_chains(self) -> list[str]:
        return self.user_chains + [self.gateway_chain_id]

    def chain_dir_name(self, chain: str) -> str:
        """Map a chain ID to its per-chain directory name."""
        if chain == self.gateway_chain_id:
            return "gateway"
        if chain in self.l1_settling_chains:
            return "l1_settling"
        idx = self.user_chains.index(chain)
        return f"gateway_settling_{chr(ord('a') + idx)}"

    def chain_output_paths(
        self, chain: str, protocol_base: Path
    ) -> tuple[Path, Path, Path]:
        d = protocol_base / self.chain_dir_name(chain)
        return (d / "config.yaml", d / "wallets.yaml", d / "contracts.yaml")

    def get_l1_settling_chains(self) -> list[str]:
        return list(self.l1_settling_chains)

    def _user_chain_rpc_port(self, chain: str) -> int:
        """RPC port for a user chain, skipping the gateway port (3052)."""
        idx = self.user_chains.index(chain)
        port = 3051 + idx
        if port >= 3052:
            port += 1
        return port

    def ensure_chain_base_config(
        self, yaml_path: Path, chain: str, protocol_version: str
    ) -> None:
        if yaml_path.exists():
            return
        if chain in self.user_chains:
            _write_chain_base_config(
                yaml_path,
                chain,
                protocol_version,
                ephemeral=True,
                gateway_rpc_url=config.GATEWAY_DEFAULT_URL,
                rpc_port=self._user_chain_rpc_port(chain),
            )
        elif chain == self.gateway_chain_id:
            _write_chain_base_config(
                yaml_path,
                chain,
                protocol_version,
                rpc_port=3052,
            )
        else:
            _write_chain_base_config(
                yaml_path,
                chain,
                protocol_version,
                ephemeral=True,
            )

    def use_blob_operator_for(self, chain: str) -> bool:
        # Gateway settles on L1, so it uses the blob operator
        return chain == self.gateway_chain_id

    def on_initial_chain_ready(
        self,
        ctx: ScriptCtx,
        ecosystem_dir: Path,
        zkstack_bin: Path,
        contracts_yaml: Path,
        wallets_yaml: Path,
        chain_operator_sks: list[str],
        protocol_base: Path,
    ) -> None:
        bridgehub_address = edit_server.get_contract_address(
            contracts_yaml, "bridgehub_proxy_addr"
        )

        ctx.logger.info("Generating L1 -> L2 deposit transaction...")
        ctx.sh(
            f"""
            cargo run --release --package zksync_os_generate_deposit --
            --bridgehub "{bridgehub_address}"
            --chain-id {self.gateway_chain_id}
            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
            --private-key {config.ANVIL_RICH_PRIVATE_KEY}
            --amount 100
            """
        )
        ctx.sh(
            f"""
            {zkstack_bin}
            chain gateway create-tx-filterer
            --chain {self.gateway_chain_id}
            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
            --ignore-prerequisites
            """,
            cwd=ecosystem_dir,
        )
        ctx.sh(
            f"""
            {zkstack_bin}
            chain gateway convert-to-gateway
            --chain {self.gateway_chain_id}
            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
            --ignore-prerequisites
            --no-gateway-overrides
            """,
            cwd=ecosystem_dir,
        )

        ctx.logger.info(
            "Generating L1 -> L2 gateway deposit transactions for chain operators..."
        )
        for sk in chain_operator_sks:
            ctx.sh(
                f"""
                cargo run --release --package zksync_os_generate_deposit --
                --bridgehub "{bridgehub_address}"
                --chain-id {self.gateway_chain_id}
                --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                --private-key {sk}
                --amount 10.0
                """
            )

    def write_l1_settling_configs(
        self,
        ctx: ScriptCtx,
        zkstack_bin: Path,
        ecosystem_dir: Path,
        protocol_version: str,
        protocol_base: Path,
    ) -> None:
        """Generate deposit txs and write configs for L1-settling chains."""
        for chain in self.l1_settling_chains:
            contracts_yaml = (
                ecosystem_dir / "chains" / chain / "configs" / "contracts.yaml"
            )
            wallets_yaml = (
                ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
            )

            bridgehub_address = edit_server.get_contract_address(
                contracts_yaml, "bridgehub_proxy_addr"
            )

            ctx.logger.info(
                f"Generating L1 -> L2 deposit transaction for "
                f"L1-settling chain {chain}..."
            )
            ctx.sh(
                f"""
                cargo run --release --package zksync_os_generate_deposit --
                --bridgehub "{bridgehub_address}"
                --chain-id {chain}
                --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                --private-key {config.ANVIL_RICH_PRIVATE_KEY}
                --amount 100
                """
            )

            config_dst, wallets_dst, contracts_dst = (
                self.chain_output_paths(chain, protocol_base)
            )
            ctx.logger.info(
                f"Writing L1-settling config for chain {chain} "
                f"to {config_dst.parent}..."
            )
            _write_chain_base_config(
                config_dst,
                chain,
                protocol_version,
                ephemeral=True,
            )
            edit_server.update_chain_config_yaml(
                config_dst,
                use_blob_operator=True,
                contracts_yaml=contracts_yaml,
                wallets_yaml=wallets_yaml,
            )
            utils.cp(wallets_yaml, wallets_dst)
            utils.cp(contracts_yaml, contracts_dst)

    def run_gateway_phase(
        self,
        ctx: ScriptCtx,
        zkstack_bin: Path,
        ecosystem_dir: Path,
        protocol_version: str,
        protocol_base: Path,
    ) -> None:
        gateway_db = ctx.workspace / "gateway-db"

        # Pre-build the server before starting the gateway
        ctx.sh("cargo build --release", cwd=ctx.repo_dir)
        utils.clean_dir(gateway_db)
        utils.remove_dir(ctx.workspace / "gateway-db.tar.gz")

        with utils.gateway(
            repo_path=ctx.repo_dir,
            db_path=gateway_db,
            protocol_version=protocol_version,
        ):
            for chain in self.user_chains:
                ctx.sh(
                    f"""
                    {zkstack_bin}
                    chain gateway notify-about-to-gateway-update
                    --chain {chain}
                    --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                    """,
                    cwd=ecosystem_dir,
                )
                ctx.sh(
                    f"""
                    {zkstack_bin}
                    chain gateway migrate-to-gateway
                    --chain {chain}
                    --gateway-chain-name {self.gateway_chain_id}
                    --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                    --gateway-rpc-url="{config.GATEWAY_DEFAULT_URL}"
                    """,
                    cwd=ecosystem_dir,
                )
                ctx.sh(
                    f"""
                    {zkstack_bin}
                    chain gateway finalize-chain-migration-to-gateway
                    --chain {chain}
                    --gateway-chain-name {self.gateway_chain_id}
                    --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                    --gateway-rpc-url="{config.GATEWAY_DEFAULT_URL}"
                    --deploy-paymaster=false
                    """,
                    cwd=ecosystem_dir,
                )

                # Set up payment of settlement fees so the migrated chain can
                # be settled on gateway. The GWAssetTracker is a gateway L2
                # system contract at a well-known fixed address.
                # Since gateway is ETH-based in this environment, fees are paid
                # in wrapped ETH, so we must:
                #   1. Deposit ETH into the wrapped base-token contract.
                #   2. Approve GWAssetTracker to pull those tokens.
                #   3. Register the operator as a fee payer for the chain.
                GW_ASSET_TRACKER = "0x0000000000000000000000000000000000010010"
                chain_wallets = utils.load_yaml(
                    ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
                )
                operator_sk = utils.normalize_hex(
                    chain_wallets["execute_operator"]["private_key"]
                )

                ctx.logger.info(
                    f"Agreeing to pay settlement fees for chain {chain}..."
                )
                wrapped_token_addr = utils.sh_output([
                    "cast", "call",
                    GW_ASSET_TRACKER,
                    "wrappedZKToken()(address)",
                    "--rpc-url", config.GATEWAY_DEFAULT_URL,
                ])
                ctx.sh(
                    f"""
                    cast send {wrapped_token_addr}
                    "deposit()"
                    --value 1ether
                    --private-key {operator_sk}
                    --rpc-url "{config.GATEWAY_DEFAULT_URL}"
                    """
                )
                ctx.sh(
                    f"""
                    cast send {wrapped_token_addr}
                    "approve(address,uint256)"
                    {GW_ASSET_TRACKER} {10**18}
                    --private-key {operator_sk}
                    --rpc-url "{config.GATEWAY_DEFAULT_URL}"
                    """
                )
                ctx.sh(
                    f"""
                    cast send {GW_ASSET_TRACKER}
                    "agreeToPaySettlementFees(uint256)"
                    {chain}
                    --private-key {operator_sk}
                    --rpc-url "{config.GATEWAY_DEFAULT_URL}"
                    """
                )

        utils.cp(
            ctx.workspace / "gateway-db.tar.gz",
            protocol_base / "gateway-db.tar.gz",
        )

        ephemeral_state = f"./local-chains/{protocol_version}/gateway-db.tar.gz"
        cfg = protocol_base / "gateway" / "config.yaml"
        data = utils.load_yaml(cfg)
        data.setdefault("general", {})["ephemeral"] = True
        data.setdefault("general", {})["ephemeral_state"] = ephemeral_state
        with cfg.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            f.write("\n")


# ---------------------------------------------------------------------------
# Main ecosystem orchestration
# ---------------------------------------------------------------------------
def init_ecosystem(
    ctx: ScriptCtx,
    setup: EcosystemSetup,
    protocol_version: str,
) -> None:
    era_contracts_path = utils.require_path("ERA_CONTRACTS_PATH")
    zksync_era_path = utils.require_path("ZKSYNC_ERA_PATH")

    zkstack_bin = zksync_era_path / "zkstack_cli" / "target" / "release" / "zkstack"
    ecosystems_dir = ctx.workspace / "ecosystems"
    ecosystem_dir = ecosystems_dir / setup.ecosystem_name
    protocol_base = ctx.repo_dir / "local-chains" / protocol_version

    with ctx.section(f"Initialize {setup.ecosystem_name} ecosystem", expected=120):
        utils.clean_dir(ecosystem_dir)
        ctx.sh(
            f"""
                {zkstack_bin}
                  ecosystem create
                  --ecosystem-name {setup.ecosystem_name}
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
        # ctm set-ctm-contracts saves this path; subsequent zkstack commands
        # (ecosystem init, chain create) read from it. Cleaned up at the end.
        ctm_defaults = protocol_base / "default"
        ctm_defaults.mkdir(parents=True, exist_ok=True)
        ctx.sh(
            f"""
                {zkstack_bin}
                  ctm set-ctm-contracts
                  --contracts-src-path {era_contracts_path}
                  --default-configs-src-path {ctm_defaults}
                  --zksync-os
                """,
            cwd=ecosystem_dir,
        )
        # Remove default era chain (non zksync-os)
        utils.clean_dir(ecosystem_dir / "chains")

        # Create the initial chain and mark it as the zkstack default so that
        # `ecosystem init` below knows which chain to deploy.
        ctx.sh(
            f"""
            {zkstack_bin}
              chain create
              --chain-name {setup.initial_chain}
              --chain-id {setup.initial_chain}
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

    with ctx.section(
        f"Generating l1-state.json for {setup.ecosystem_name}", expected=250
    ):
        l1_state_file = protocol_base / "l1-state.json"
        with utils.anvil_dump_state(l1_state_file=l1_state_file):
            ctx.logger.info("Funding accounts...")
            fund_accounts(ctx, ecosystem_dir)

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

            # Create and initialise every user chain that wasn't already
            # deployed by `ecosystem init` (i.e. all except the initial chain).
            for chain in setup.user_chains:
                if chain == setup.initial_chain:
                    continue
                _create_and_init_chain(ctx, zkstack_bin, ecosystem_dir, chain)

            # Create and initialise L1-settling chains (not migrated to gateway).
            # Do not pause deposits so generate_deposit can submit L1->L2 tx.
            for chain in setup.get_l1_settling_chains():
                _create_and_init_chain(
                    ctx, zkstack_bin, ecosystem_dir, chain, pause_deposits=False
                )

            # Collect operator SKs from all user chains; used by GatewaySetup
            # to fund chain operators on the gateway via L1->Gateway deposits.
            chain_operator_sks = _collect_operator_sks(ecosystem_dir, setup.user_chains)

            # Write config files and copy wallets/contracts for every chain.
            for chain in setup.all_chains:
                ctx.logger.debug(f"Updating contract addresses for chain {chain}...")
                contracts_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "contracts.yaml"
                )
                chain_wallets_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
                )
                config_dst, wallets_dst, contracts_dst = (
                    setup.chain_output_paths(chain, protocol_base)
                )
                setup.ensure_chain_base_config(
                    config_dst, chain, protocol_version
                )
                edit_server.update_chain_config_yaml(
                    config_dst,
                    use_blob_operator=setup.use_blob_operator_for(chain),
                    contracts_yaml=contracts_yaml,
                    wallets_yaml=chain_wallets_yaml,
                )
                utils.cp(chain_wallets_yaml, wallets_dst)
                utils.cp(contracts_yaml, contracts_dst)

                if chain == setup.initial_chain:
                    setup.on_initial_chain_ready(
                        ctx,
                        ecosystem_dir,
                        zkstack_bin,
                        contracts_yaml,
                        chain_wallets_yaml,
                        chain_operator_sks,
                        protocol_base,
                    )

            setup.run_gateway_phase(
                ctx, zkstack_bin, ecosystem_dir, protocol_version, protocol_base
            )

            setup.write_l1_settling_configs(
                ctx, zkstack_bin, ecosystem_dir, protocol_version, protocol_base
            )

    utils.remove_dir(protocol_base / "default")


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

    # ------------------------------------------------------------------ #
    # Select ecosystem setup
    #
    #   v30.2                    → NoGatewaySetup (gateway not supported)
    #   v31.0, USE_GATEWAY=true  → GatewaySetup   (default for v31.0)
    #   v31.0, USE_GATEWAY=false → NoGatewaySetup (opt-out for testing)
    # ------------------------------------------------------------------ #
    user_chains = ["6566", "6567"]
    l1_settling_chains = ["6565"]

    if Version(protocol_version) == Version(PROTOCOL_VERSION_CURRENT):
        setup: EcosystemSetup = NoGatewaySetup("multi_chain", user_chains)
    elif Version(protocol_version) >= Version(PROTOCOL_VERSION_NEXT):
        use_gateway_raw = utils.require_env("USE_GATEWAY", "true")
        if use_gateway_raw.lower() not in ("true", "false"):
            raise SystemExit(
                f"USE_GATEWAY must be 'true' or 'false', got: {use_gateway_raw!r}"
            )
        if use_gateway_raw.lower() == "true":
            setup = GatewaySetup(
                "multi_chain",
                user_chains,
                config.GATEWAY_CHAIN_ID,
                l1_settling_chains,
            )
        else:
            # Without a gateway every user chain already settles on L1,
            # so l1_settling_chains are not needed.
            setup = NoGatewaySetup("multi_chain", user_chains)
    else:
        raise ValueError(
            f"Unsupported PROTOCOL_VERSION: {protocol_version}. Supported: {list(PROTOCOL_TOOLCHAINS.keys())}"
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
    # Clean generated artifacts (keep READMEs)
    # ------------------------------------------------------------------ #
    protocol_base = ctx.repo_dir / "local-chains" / protocol_version
    utils.clean_dir_keep_readmes(protocol_base)

    # ------------------------------------------------------------------ #
    # Generate genesis.json
    # ------------------------------------------------------------------ #
    with ctx.section("Generate genesis.json", expected=60):
        ctx.sh(
            f"""
            cargo run --
              --output-file {ctx.repo_dir / "local-chains" / protocol_version / "genesis.json"}
            """,
            cwd=era_contracts_path / "tools" / "zksync-os-genesis-gen",
        )

    # ------------------------------------------------------------------ #
    # Multi-chain setup
    # ------------------------------------------------------------------ #
    init_ecosystem(ctx, setup, protocol_version)

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
