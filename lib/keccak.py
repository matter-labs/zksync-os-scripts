"""Keccak-256 as Solidity's `keccak256` computes it.

`hashlib.sha3_256` is FIPS-202 SHA3-256, which pads differently and gives other
digests, and the project has no dependency that provides Keccak-256.
"""

_MASK = (1 << 64) - 1
# Bytes absorbed per permutation for a 256-bit output.
_RATE = 136
_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
# Rotation offsets, indexed [x][y].
_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _permute(lanes: list[int]) -> list[int]:
    """Keccak-f[1600] over 25 lanes, lane (x, y) at index x + 5 * y."""
    for round_constant in _ROUND_CONSTANTS:
        columns = [
            lanes[x] ^ lanes[x + 5] ^ lanes[x + 10] ^ lanes[x + 15] ^ lanes[x + 20]
            for x in range(5)
        ]
        theta = [
            columns[(x - 1) % 5] ^ _rotl(columns[(x + 1) % 5], 1) for x in range(5)
        ]
        lanes = [lane ^ theta[index % 5] for index, lane in enumerate(lanes)]
        moved = [0] * 25
        for x in range(5):
            for y in range(5):
                moved[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(
                    lanes[x + 5 * y], _ROTATIONS[x][y]
                )
        lanes = [
            moved[x + 5 * y]
            ^ (~moved[(x + 1) % 5 + 5 * y] & _MASK & moved[(x + 2) % 5 + 5 * y])
            for y in range(5)
            for x in range(5)
        ]
        lanes[0] ^= round_constant
    return lanes


def keccak256(data: bytes) -> bytes:
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(bytes(-len(padded) % _RATE))
    padded[-1] |= 0x80
    lanes = [0] * 25
    for offset in range(0, len(padded), _RATE):
        block = padded[offset : offset + _RATE]
        for index in range(_RATE // 8):
            lanes[index] ^= int.from_bytes(block[index * 8 : index * 8 + 8], "little")
        lanes = _permute(lanes)
    return b"".join(lane.to_bytes(8, "little") for lane in lanes[:4])
