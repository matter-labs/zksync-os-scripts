# ZKsync OS Scripts

Operational scripts for ZKsync OS protocol upgrades.

This repository contains a collection of Python utilities used to safely and reproducibly perform ZKsync OS upgrades, including:
* [Updating verification keys for the contracts](./scripts/update_vk.md)
* [Updating genesis and L1 state for the ZKsync OS server](./scripts/update_server.md)
* [Updating prover configurations with new ZKsync OS version](./scripts/update_prover.md)
* [Updating ZKsync OS wrapper with the new ZKsync OS version](./scripts/update_wrapper.md)

The scripts are written in shell-like style, and are designed to be explicit, easy to contribute, and automation-friendly.

```admonish tip
If you would like to use automation to do the job for you, jump directly to the [GitHub Actions guide](./github-actions.md).

If you need a local setup, please proceed to the [Prerequisites](./prerequisites.md) document to install all necessary dependencies.
```

---

## Why Python?

Python provides a good balance between readability, ease of use, and powerful libraries for scripting and automation.
It allows us to write clear and maintainable code that can be easily understood by team members.

Shell scripts can become complex and hard to manage as the project grows, while Python offers better structure and error handling.

In addition, Python does not require third-party dependencies for most common tasks,
making it easier to set up and run the scripts in various environments.

## Why not Rust?

While Rust is a powerful language and widely used across Matter Labs, it has a longer development time for scripting tasks compared to Python.
Python's dynamic typing and extensive standard library make it more suitable for quick prototyping and iterative development without compilation.
