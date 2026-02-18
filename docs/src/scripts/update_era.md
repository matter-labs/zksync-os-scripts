# Update era setup data

Updates `zksync-era` prover setup data using the `key_generator` binary for a selected protocol version.

```admonish title="Script path"
[https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/era/update_era.py](https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/era/update_era.py)
```

Use this script when you need to regenerate ZKsync Era prover setup.

Script performs the following steps:
- Builds `key_generator` with GPU support.
- Generates base layer setup data for required circuits.
- Generates recursive layer setup data for required circuits.
- Generates compressor data.

---

## Local use

~~~admonish example title="Example usage"
```bash
REPO_DIR="/path/to/zksync-era" \
BELLMAN_CUDA_DIR="/path/to/bellman-cuda" \
    uv run -m scripts.era.update_era
```
~~~

To run the script, you will need:

- [General prerequisites including uv, Rust and NodeJS](../prerequisites.md)
- Access to the `zksync-era` repository specified via `REPO_DIR`
- `PROTOCOL_VERSION` supported by the script (currently defaults to `v30`)
- Access to Bellman CUDA specified via `BELLMAN_CUDA_DIR`
