# Protocol compatibility

This page is the source of truth for the compatibility guarantees provided by ZKsync OS scripts across different protocol versions.

```admonish warning title="Using proper versions"
When using the scripts, ensure that you check out the appropriate branches of the repositories as per the protocol version you are targeting.
```

## Compatibility between server, contracts, and zkstack CLI

| protocol  | zksync-os-server | era-contracts      | zksync-era (zkstack_cli) |
| :-------: | ---------------- | ------------------ | ------------------------ |
| **v30.2** | `main`           | `zksync-os-stable` | `zkstack-for-zksync-os`  |
| **v31.0** | `main`           | `draft-v31`        | `draft-v31`              |

## Compatibility for foundry versions

| protocol  | foundry-zksync                                     | anvil version |
| :-------: | -------------------------------------------------- | ------------- |
| **v30.2** | `nightly-ae913af65381734ad46c044a9495b67310bc77c4` | `1.5.1`       |
| **v31.0** | `1.3.5-foundry-zksync-v0.1.5`                      | `1.5.1`       |
