from dataclasses import dataclass


# Protocol version v30
PROTOCOL_V30: str = "v30"
# Protocol version v31.0
PROTOCOL_V31_0: str = "v31.0"

# Current protocol version
PROTOCOL_VERSION_CURRENT: str = PROTOCOL_V30
# Next supported protocol version
PROTOCOL_VERSION_NEXT: str = PROTOCOL_V31_0


@dataclass(frozen=True)
class Toolchain:
    """Default versions associated with a protocol version."""

    cast_forge_version: str
    cargo_version: str
    yarn_version: str


PROTOCOL_TOOLCHAINS: dict[str, Toolchain] = {
    PROTOCOL_V30: Toolchain(
        yarn_version="1.22",
        cast_forge_version="0.0.4",
        cargo_version="1.80.0",
    ),
}
