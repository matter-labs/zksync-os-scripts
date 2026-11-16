# Update ZiSK verification keys

Rotates the ZiSK range verifier in [era-contracts](https://github.com/matter-labs/era-contracts)
to a [zksync-os-zisk release](https://github.com/matter-labs/zksync-os-zisk/releases).

```admonish title="Script path"
[https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_zisk_vk.py](https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_zisk_vk.py)
```

ZiSK is the second prover lane of protocol v34. era-contracts' `ZiskVerifier` pins three values
from a zksync-os-zisk release, and the release commit records each of them:

| Pin | Record in zksync-os-zisk |
| --- | --- |
| inner program VK | `guest/GUEST_PROGRAM_VK` |
| aggregator program VK | `guest-aggregator/GUEST_PROGRAM_VK` |
| vadcop-final root | `guest-aggregator/ROOT_C_VADCOP_FINAL` |

The script performs the following steps:
- Resolves the release tag to its commit and reads the three records there. When a record lists
  the value's u64 limbs, the hex value must match them.
- Reads the ZiSK version from the guests' exact `ziskos` requirement (`guest/Cargo.toml` and
  `guest-aggregator/Cargo.toml`, which must agree).
- Compares the committed final PLONK key, `tools/verifier-gen/data/ZiSK_plonk_verification_key.json`,
  with upstream ZiSK's `zisk-contracts/PlonkVerifier.sol` at `v<version>`. All 25 fields must match:
  domain size, public input count, omega, the two coset constants, the eight circuit commitments
  and the SRS point. A mismatch stops the run before anything is written, because a new final
  PLONK circuit also needs a new deployed PLONK backend (`zisk_plonk_verifier_addr`).
- Writes the pins into `tools/verifier-gen/data/ZiSK_vk.json` as big-endian u64 limbs.
- Regenerates `ZiskVerifier` with `cargo run -- --variant zisk` and checks that its VK hash is
  `keccak256` over the three pins.
- Moves `MultiProofRangeVectorTest` to the pins and its binding digest,
  `keccak256(innerVK ‖ rootC ‖ CHAINED_PI)`. `CHAINED_PI` is frozen in the test, so the digest
  needs no proof.
- Recomputes `AllContractsHashes.json` with `recompute_hashes.sh`.
- Fails if the rotation changed any other file in the checkout.

It updates the following files in [era-contracts](https://github.com/matter-labs/era-contracts):

- `tools/verifier-gen/data/ZiSK_vk.json` - the pins
- `l1-contracts/contracts/state-transition/verifiers/ZiskVerifier.sol` - the regenerated verifier
- `l1-contracts/test/foundry/l1/unit/concrete/Verifier/MultiProofRangeVectorTest.t.sol` - pins and binding digest
- `AllContractsHashes.json` - recomputed contract hashes

[era-contracts#2622](https://github.com/matter-labs/era-contracts/pull/2622) is a manual rotation
to release `0.0.9` and shows the expected changes.

```admonish warning title="Not covered"
- Real-proof fixtures. `ZiskVerifierRealProofTest` keeps the previous release's proofs until a
  separate run of zksync-os-zisk's [`fixture-session.yaml`](https://github.com/matter-labs/zksync-os-zisk/actions/workflows/fixture-session.yaml),
  which proves on the RTX 6000 runner. Without a PLONK backend, era-contracts CI skips that suite.
- Prose that names the release: `l1-contracts/contracts/state-transition/verifiers/README.md`
  and the notice of `MultiProofRangeVectorTest`.

The generated PR description lists both.
```

---

## Local use

~~~admonish example title="Example usage"
```bash
WORKSPACE=/tmp/zisk-vk \
REPO_DIR=/path/to/era-contracts \
ZISK_RELEASE_TAG=0.0.9 \
UPSTREAM_FOUNDRY_DIR=~/.foundry/versions/v1.5.1 \
    uv run -m scripts.update_zisk_vk
```
~~~

To run the script, you will need:
- [General prerequisites including uv, Rust and NodeJS](../prerequisites.md); era-contracts'
  `recompute_hashes.sh` needs Node 20 or newer
- An authenticated GitHub CLI (`gh auth login` or `GH_TOKEN`)
- An era-contracts checkout with a ZiSK verifier (`draft/v0.34.0` onwards)
- The upstream Foundry build era-contracts pins in `.github/foundry-versions.env`, through
  `UPSTREAM_FOUNDRY_DIR` (see [contracts that pin upstream Foundry](./update_vk.md#contracts-that-pin-upstream-foundry))

| Variable | Required | Description | Default |
| --- | --- | --- | --- |
| `ZISK_RELEASE_TAG` | ✅ | Release tag to pin, e.g. `0.0.9`. | — |
| `ZISK_REPOSITORY` | ❌ | Release repository (`owner/name`). | `matter-labs/zksync-os-zisk` |
| `ZISK_UPSTREAM_REPOSITORY` | ❌ | Upstream ZiSK repository (`owner/name`). | `0xPolygonHermez/zisk` |
| `UPSTREAM_FOUNDRY_DIR` | ❌ | Directory of the upstream Foundry build for `recompute_hashes.sh`. | inherited `PATH` |
| `RUN_URL` | ❌ | Workflow run linked from the PR description. | — |

Besides the contracts changes, the script writes two files to the workspace:
`zisk-rotation.json` (release commit, ZiSK version, new and previous pins with their limbs,
VK hashes and binding digest) and `zisk_pr_body.md` (the PR description).

---

## GitHub Actions

The [`update-zisk-vk.yaml` workflow](https://github.com/matter-labs/zksync-os-scripts/blob/main/.github/workflows/update-zisk-vk.yaml)
runs the script and can be triggered manually via the GitHub Actions UI.

```admonish title="Workflow link"
[https://github.com/matter-labs/zksync-os-scripts/actions/workflows/update-zisk-vk.yaml](https://github.com/matter-labs/zksync-os-scripts/actions/workflows/update-zisk-vk.yaml)
```

### Input parameters

| Name | Required | Description |
| --- | --- | --- |
| `zisk_release_tag` | ✅ | zksync-os-zisk release tag to pin, e.g. `0.0.9`. |
| `era_contracts_branch` | ✅ | Contracts branch to update, e.g. `draft/v0.34.0`. |
| `commit_changes` | ❌ | Whether to commit the changes. Defaults to `true` (`false` for reusable workflow callers). |
| `open_pr` | ❌ | Whether to open a PR. Defaults to `true` (`false` for reusable workflow callers). |

```admonish tip title="Configuring the workflow"
Follow more detailed tutorial in the [GitHub Actions guide](../github-actions.md).
```

### Outputs

On **successful runs**, the workflow uploads `era-contracts-zisk-patch-<tag>`, holding
`era_contracts_zisk_vk.patch`, and `zisk-rotation-<tag>`, holding `zisk-rotation.json` and
`zisk_pr_body.md`.

If `commit_changes` and `open_pr` are set to `true`, the workflow opens a PR from
`update-zisk-vk-<tag>-<timestamp>`, titled
`chore(verifier): rotate ZiSK pins to zksync-os-zisk release <tag>`, with `zisk_pr_body.md` as its
description. Without `open_pr` it pushes onto `era_contracts_branch`. If the branch already pins
the release, nothing is committed.

Like [`update-vk.yaml`](./update_vk.md#outputs), the workflow commits through
`.github/scripts/push-signed-commit.sh`, so GitHub signs the commit, which era-contracts requires.

On **failed runs**, the workflow saves logs from the workspace `.logs` directory as an artifact.

### Tokens

zksync-os-zisk and upstream ZiSK are public, so reading them needs an authenticated token but no
repository grant; anonymous API calls are limited to 60 an hour per runner IP. The rotation step
reads with `RELEASE_TOKEN` and falls back to the workflow token when the secret is unavailable.

It does not use the `MATTERLABS_BOTS` app. `update-vk.yaml` scopes that app's tokens to the
private repositories it reads, and a token cannot be scoped to a repository outside the app's
installation (public era-contracts is outside it). A public repository does not need the app.

The era-contracts checkout and commit use the same tokens as `update-vk.yaml`: the workflow token
for dry runs, and `RELEASE_TOKEN` when `commit_changes` is set. `RELEASE_TOKEN` needs write
access to era-contracts and authors the signed commit and the PR.

## Script dependencies

- Pin records and guest manifests at the [zksync-os-zisk](https://github.com/matter-labs/zksync-os-zisk) release commit
- [`zisk-contracts/PlonkVerifier.sol`](https://github.com/0xPolygonHermez/zisk/tree/main/zisk-contracts) at the matching ZiSK release
- [ZiSK verifier generator](https://github.com/matter-labs/era-contracts/tree/draft/v0.34.0/tools/verifier-gen) (`--variant zisk`)
- [`recompute_hashes.sh`](https://github.com/matter-labs/era-contracts/blob/draft/v0.34.0/recompute_hashes.sh)

```admonish bug title="Script failure"
- PLONK key mismatch → ZiSK changed its final PLONK circuit
  - To fix: rotate `ZiSK_plonk_verification_key.json` and deploy a new backend by hand, then rerun
- Record or manifest parse errors → zksync-os-zisk changed a record's layout
  - To fix: align `update_zisk_vk.py` with the new layout
- Files changed outside the rotation → a tool in the contracts checkout changed more than expected
  (for example `yarn.lock`)
  - To fix: check the files the error lists against the contracts branch
```
