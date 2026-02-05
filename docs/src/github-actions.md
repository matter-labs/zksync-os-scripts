# Running in GitHub Actions

Use this guide if you want to run the scripts via **GitHub Actions CI** instead of executing them locally.

Manual GitHub Actions workflows are available for the following scripts:
  - [Update era-contracts: verification key generation](./scripts/update_vk.md)
  - [Update server: genesis and l1 state](./scripts/update_server.md)

```admonish warning
This documentation assumes you have permission to run workflows in Matter Labs repositories.
```

## How to run the workflows

1. Open [the workflow page](https://github.com/matter-labs/zksync-os-scripts/actions)
2. Choose the desired workflow from the left sidebar
2. Click **Run workflow** on the upper right side
3. Fill in the input parameters
4. Click **Run workflow** on the bottom to start the job

## Available workflows

Please, jump to the corresponding script section for more details on each workflow:
* [Update era-contracts: verification key generation](./scripts/update_vk.md#github-actions)
* [Update server: genesis and l1 state](./scripts/update_server.md#github-actions)

## Outputs and artifacts

On **successful runs**, the workflow uploads, for example:

- `server_<protocol_version>.patch`
- `contracts_<protocol_version>.patch`

On **failed runs**, the workflow uploads:

- Logs from the workspace `.logs` directory

```admonish tip
Artifacts can be downloaded directly from the workflow run page in GitHub Actions.
```

## Commit and PR behavior

- `commit_changes = false`
  - No commit is created
  - A `.patch` file is uploaded as an artifact
- `commit_changes = true` and `open_pr = false`
  - Changes are pushed directly to the selected base branch
- `commit_changes = true` and `open_pr = true`
  - A temporary branch is created
  - A PR is opened automatically

## When to use CI vs local execution

```admonish tip
Choosing the right execution mode saves time.
```

Use **GitHub Actions** when:

- You want reproducible and reliable updates
- You want consistent environment and tooling versions

Use **local scripts** when:

- Debugging or developing scripts
- Iterating on update logic
- Testing experimental changes
