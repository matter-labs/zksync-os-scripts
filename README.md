# zksync-os-scripts

[![Logo](.github/assets/zksync-os-logo.png)](https://zksync.io/)

## [Prerequisites](https://matter-labs.github.io/zksync-os-scripts/latest/prerequisites) | [User docs](https://matter-labs.github.io/zksync-os-scripts/latest/index) | [Developer docs](https://matter-labs.github.io/zksync-os-scripts/latest/developer/index) | [![CI](https://github.com/matter-labs/zksync-os-server/actions/workflows/ci.yml/badge.svg)](https://github.com/matter-labs/zksync-os-scripts/actions/workflows/ci.yml)

Operational scripts for ZKsync OS protocol upgrades.

This repository contains a collection of Python utilities used to safely and reproducibly perform ZKsync OS upgrades, including:
- [Update zkos-wrapper: prepare for verification key generation](https://matter-labs.github.io/zksync-os-scripts/latest/scripts/update_wrapper)
- [Update era-contracts: verification key generation](https://matter-labs.github.io/zksync-os-scripts/latest/scripts/update_vk)
- [Update server: genesis and l1 state](https://matter-labs.github.io/zksync-os-scripts/latest/scripts/update_server)
- [Update prover: prepare for the protocol upgrade](https://matter-labs.github.io/zksync-os-scripts/latest/scripts/update_prover)

The scripts are written in shell-like style, and are designed to be explicit, easy to contribute, and automation-friendly.

## Getting started

Follow the instructions in the [Prerequisites](https://matter-labs.github.io/zksync-os-scripts/latest/prerequisites) document to set up your environment.

Check [protocol compatiblity tables](https://matter-labs.github.io/zksync-os-scripts/latest/protocol-compatibility) for details on dependency and toolchain versions used for a particular protocol version.

Explore the [User documentation](https://matter-labs.github.io/zksync-os-scripts/latest/index) to understand how to use the scripts.

If you are interested in contributing or understanding the internal workings, check out the [Developer documentation](https://matter-labs.github.io/zksync-os-scripts/latest/developer/index).

## GitHub Actions

If you are interested in automated execution of the scripts via GitHub Actions,
please refer to the [GitHub Actions guide](https://matter-labs.github.io/zksync-os-scripts/latest/github-actions).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for contribution guidelines.

## Security

See [SECURITY.md](./SECURITY.md) for security policy details.

## Policies

- [Security policy](SECURITY.md)
- [Contribution policy](CONTRIBUTING.md)

## License

ZKsync OS repositories are distributed under the terms of either

- Apache License, Version 2.0, ([LICENSE-APACHE](LICENSE-APACHE) or <http://www.apache.org/licenses/LICENSE-2.0>)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or <https://opensource.org/blog/license/mit/>)

at your option.

## Official Links

- [Website](https://zksync.io/)
- [GitHub](https://github.com/matter-labs)
- [ZK Credo](https://github.com/zksync/credo)
- [Twitter](https://twitter.com/zksync)
- [Twitter for Developers](https://twitter.com/zkSyncDevs)
- [Discord](https://join.zksync.dev/)
- [Mirror](https://zksync.mirror.xyz/)
- [Youtube](https://www.youtube.com/@zksync-io)
