# Update ZKsync OS wrapper

Updates [zkos-wrapper](https://github.com/matter-labs/zkos-wrapper) repository with the new SNARK proof
to prepare verification key generation on the next step using [`update_vk.py`](./update_vk.md) script.

```admonish title="Script path"
[https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_wrapper.py](https://github.com/matter-labs/zksync-os-scripts/blob/main/scripts/update_wrapper.py)
```

---

## Local use

~~~admonish example title="Example usage"
```bash
REPO_DIR="/path/to/zkos-wrapper" \
ZKSYNC_AIRBENDER_PATH=/path/to/zksync-airbender \
    uv run -m scripts.update_wrapper
```
~~~

To run the script, you will need:

- [General prerequisites including uv, Rust and NodeJS](../prerequisites.md)
- Access to the `zkos-wrapper` repository specified through `REPO_DIR` env variable
- Access to the `zksync-airbender` repository specified through `ZKSYNC_AIRBENDER_PATH` env variable

