# Environment variables

All scripts are configured via environment variables. This allows the same scripts to run
locally, in CI, or against different repositories without code changes and not overloading scripts with command-line arguments.

```admonish tip title="Setting environment variables"
Environment variables fall into three categories:

1. **Common environment variables**<br>
   Used by all scripts to control general behavior such as logging and workspace layout.

2. **Script-specific environment variables**<br>
   Used only by a particular script to customize its behavior.

3. **Path environment variables**<br>
   Used to point scripts to required checked-out repositories.

```

## Common environment variables

These variables are supported by all scripts unless stated otherwise.

| Variable     | Required | Description                                                                 | Default      |
|--------------|----------|-----------------------------------------------------------------------------|--------------|
| `REPO_DIR`   | ✅ Yes   | Path to the repository that will be modified by the script.                 | —            |
| `WORKSPACE`  | ❌ No    | Temporary working directory for intermediate files.                         | `.workspace` |
| `VERBOSE`    | ❌ No    | Enable verbose logging and subcommands output (`1` or `true`).              | `false`      |

## Script-specific variables

Some scripts require additional environment variables. For example:
- `PROTOCOL_VERSION` — used by server update scripts
- `ZKSYNC_OS_TAG` — used when selecting a specific ZKsync OS tag for updates

## Example

The following example shows how to run a script with all required environment variables
defined inline:

~~~admonish example title="Example usage"
```bash
WORKSPACE="${PWD}/.custom_workspace" \      # Optional: specify custom workspace path
VERBOSE=true \                              # Optional: enable verbose logging
REPO_DIR="/path/to/era-contracts" \         # Required: path to repo for updating
ZKOS_WRAPPER_PATH="/path/to/zkos-wrapper" \ # Required: script-specific path
ZKSYNC_OS_TAG=latest \                      # Required: script-specific variable
    uv run -m scripts.update_vk
```
~~~
