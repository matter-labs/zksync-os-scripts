from dataclasses import dataclass


# Protocol version v30.2
PROTOCOL_V30_2: str = "v30.2"
# Protocol version v31.0
PROTOCOL_V31_0: str = "v31.0"

# Current protocol version
PROTOCOL_VERSION_CURRENT: str = PROTOCOL_V30_2
# Next supported protocol version
PROTOCOL_VERSION_NEXT: str = PROTOCOL_V31_0


@dataclass(frozen=True)
class Toolchain:
    """Default versions associated with a protocol version."""

    execution_version: str
    proving_version: str
    anvil_version: str
    cast_forge_version: str
    cargo_version: str
    yarn_version: str


PROTOCOL_TOOLCHAINS: dict[str, Toolchain] = {
    PROTOCOL_V30_2: Toolchain(
        yarn_version="1.22",
        execution_version="5",
        proving_version="6",
        anvil_version="1.6.0+5bcdddc06abe5b0cd8e9bc1de8ddfb7202a95ed1",
        cast_forge_version="0.0.4",
        cargo_version="1.89.0",
    ),
    PROTOCOL_V31_0: Toolchain(
        yarn_version="1.22",
        execution_version="5",  # TODO switch to 6 when supported
        proving_version="6",  # TODO switch to 7 when supported
        anvil_version="1.6.0+5bcdddc06abe5b0cd8e9bc1de8ddfb7202a95ed1",
        cast_forge_version="1.3.5",
        cargo_version="1.89.0",
    ),
}
