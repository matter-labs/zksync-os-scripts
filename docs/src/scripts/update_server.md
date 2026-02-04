# Update server: generate genesis and L1 state

Updates [zksync-os-server and its local-chains setup](https://github.com/matter-labs/zksync-os-server/tree/main/local-chains)
repository with the new genesis and L1 state.

```admonish title="Script path"
[https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_server.py](https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_server.py)
```

Usually, the script is used by the protocol upgrade operator or automation to perform the protocol upgrade.

It can be used to create or update local test setups for a given protocol version or with a custom version of [era-contracts](https://github.com/matter-labs/era-contracts).

Script performs the following steps:

- Build zkstack CLI: Compiles the zkstack command used to create/init ecosystems and chains.
- Build L1 contracts.
- Generate `genesis.json`.
- Fund necessary rich wallets.
- Deploy required L1 contracts.
- Update local-chains configurations, copies required chains and wallets files.
- Generate L1 -> L2 deposit tx for rich wallets.
- Updates verification key hash.
- Regenerate `contracts.json` used by the L1 watcher.

The script updates the following files in the [zksync-os-server](https://github.com/matter-labs/zksync-os-server) repository:

- `local-chains/<protocol_version>/**/*` - local chain configuration for chosen protocol version
- `lib/l1_watcher/src/factory_deps/contracts.json` - L1 contracts configuration used by the L1 watcher
- `lib/types/src/protocol/proving_version.rs` - new verification key hash (if it changed, otherwise it is not updated)

---

## Local use

~~~admonish example title="Example usage"
```bash
REPO_DIR="/path/to/zksync-os-server" \
ERA_CONTRACTS_PATH="/path/to/era-contracts" \
ZKSYNC_ERA_PATH="/path/to/zksync-era" \
PROTOCOL_VERSION=<protocol_version> \
    uv run -m scripts.update_server
```
~~~

```admonish warning title="Careful"
Before running this script, ensure that anvil is not running on 8545 port.
```

To run the script, you will need:

- [General prerequisites including uv, Rust and NodeJS](../prerequisites.md)
- Specific protocol version specified through `PROTOCOL_VERSION` env variable
  - Available protocol versions are defined in the [local-chains](https://github.com/matter-labs/zksync-os-server/tree/main/local-chains) directory of the `zksync-os-server` repository as subdirectories.
    For example, `v30.2` or `v31.0`.

```admonish warning title="Protocol version and branches"

In addition, the script requires access to the following repositories,
which should be specified via corresponding path environment variables:

| Repository    | Env Variable        | Protocol version → Branch Mapping |
| :------------ | :------------------ | :----------------------- |
| [zksync-os-server](https://github.com/matter-labs/zksync-os-server) | `REPO_DIR`          | **all versions** → `main` |
| [era-contracts](https://github.com/matter-labs/era-contracts) | `ERA_CONTRACTS_PATH`          | **v30.2** → `zksync-os-stable`<br />**v31.0** → `draft-v31` |
| [zksync-era](https://github.com/matter-labs/zksync-era)  | `ZKSYNC_ERA_PATH` | **v30.2** → `zkstack-for-zksync-os`<br />**v31.0** → `draft-v31`  |

Also, make sure you have the right tooling versions:

| protocol  | foundry-zksync                                     | anvil version |
| :-------: | -------------------------------------------------- | ------------- |
| **v30.2** | `nightly-ae913af65381734ad46c044a9495b67310bc77c4` | `1.5.1`       |
| **v31.0** | `1.3.5-foundry-zksync-v0.1.5`                      | `1.5.1`       |

Detailed instructions on how to install the required tooling can be found in the [prerequisites](../prerequisites.md) guide.

Please, additionally check [protocol compatibility](../protocol-compatibility.md) to ensure the correct versions are used.
```

---

## GitHub Actions

The script supports execution via GitHub Actions [`update-server.yaml` workflow](https://github.com/matter-labs/zksync-os-scripts/blob/main/.github/workflows/update-server.yaml)
that can be triggered manually via GitHub Actions UI interface.

To trigger the workflow, go to the
[Actions](https://github.com/matter-labs/zksync-os-scripts/actions) tab,
select `Update verification key` workflow from the left sidebar, and click the `Run workflow` button.

```admonish tip title="Configuring the workflow"
Follow more detailed tutorial in the [GitHub Actions guide](../github-actions.md).
```

## Script dependencies

The script relies on the following external resources:
- [zkstack_cli to init ecosystem and chains](https://github.com/matter-labs/zksync-era/tree/zkstack-for-zksync-os/zkstack_cli)
- [ZKsync OS genesis gen tool](https://github.com/matter-labs/era-contracts/tree/zksync-os-stable/tools/zksync-os-genesis-gen)
- [L1 -> L2 deposit tool](https://github.com/matter-labs/zksync-os-server/tree/main/tools/generate-deposit)


```admonish bug title="Script failure"
If the script fails, it means that one of the dependencies is changed or unavailable.
In this case, the script needs to be updated accordingly.

- Wrong tooling versions → incompatible with the contracts and scripts
  - To fix: check the required versions in the `protocol version and branches` table above and in the [protocol compatibility](../protocol-compatibility.md) guide, and install the correct versions
- Unable to start anvil → anvil is already running on 8545 port
  - To fix: stop another anvil instances you already have or change the port it runs on

Any other issue in the operations performed by the script is likely caused by changes in the dependencies,
and requires checking the steps of the script and updating it accordingly.

If you notice any such changes are required, please, reach out to the owners or create a PR with the required updates.
```

