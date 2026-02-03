# Update prover with new ZKsync OS binary

Updates ZKsync Airbender Prover with a new ZKsync OS binary.

```admonish title="Script path"
[https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_prover.py](https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_prover.py)
```

Use this script when you need to update the prover with a new ZKsync OS binary after a new ZKsync OS release.

Script performs the following steps:
- Replaces ZKsync OS binary used by the prover with a new version corresponding to the specified release tag.
- Updates prover configuration to reference the new binary and its associated parameters.

---

## Local use

~~~admonish example title="Example usage"
```bash
REPO_DIR="/path/to/zksync-airbender-prover" \
ZKSYNC_OS_TAG=<new_zksync_os_tag> \
    uv run -m scripts.update_prover
```
~~~

To run the script, you will need:

- [General prerequisites including uv, Rust and NodeJS](../prerequisites.md)
- Specific release tag of ZKsync OS from [`zksync-os` releases](https://github.com/matter-labs/zksync-os/releases) specified through `ZKSYNC_OS_TAG` env variable
