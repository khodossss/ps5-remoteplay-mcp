"""Pure-numpy stand-in for `pyjerasure` (Cauchy Reed-Solomon over GF(2^8)).

The vendored pyremoteplay uses it to recover lost video packets (FEC); the C extension
`pyjerasure` has no Windows wheels, and without FEC every lost packet drops the frame —
fatal on Wi-Fi. Only the subset pyremoteplay calls is implemented:
`Matrix("cauchy", k, m, 8)`, `align_size`, `decode_from_bytes`.
Math mirrors jerasure: cauchy_original_coding_matrix + jerasure_matrix_decode,
GF(2^8) with primitive polynomial 0x11d.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

_POLY = 0x11D
_EXP = np.zeros(512, dtype=np.uint8)
_LOG = np.zeros(256, dtype=np.int32)
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= _POLY
_EXP[255:510] = _EXP[:255]

# MUL[a] is the lookup row "multiply every byte by a".
_MUL = np.zeros((256, 256), dtype=np.uint8)
for _a in range(1, 256):
    _MUL[_a, 1:] = _EXP[_LOG[_a] + _LOG[np.arange(1, 256)]]


def _inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("GF(256) inverse of 0")
    return int(_EXP[255 - _LOG[a]])


def _mul(a: int, b: int) -> int:
    return int(_MUL[a, b])


class Matrix:
    """Coding matrix; only the `cauchy` type with w=8 is supported."""

    def __init__(self, matrix_type: str, k: int, m: int, w: int = 8) -> None:
        if matrix_type != "cauchy" or w != 8:
            raise ValueError("only cauchy matrices with w=8 are supported")
        if k + m > 256:
            raise ValueError("k + m must be <= 256 for w=8")
        self.type, self.k, self.m, self.w = matrix_type, k, m, w
        # jerasure cauchy_original_coding_matrix: 1 / (i XOR (m + j))
        self.rows = np.array(
            [[_inv(i ^ (m + j)) for j in range(k)] for i in range(m)], dtype=np.uint8
        ).reshape(m, k)

    @property
    def valid(self) -> bool:
        return True

    def align_size(self, size: int, packetsize: int = 0) -> int:
        return align_size(self, size, packetsize)


def align_size(matrix: Matrix, size: int, packetsize: int = 0) -> int:
    """Round `size` up to the 16-byte block width jerasure requires."""
    return (size + 15) // 16 * 16


def _gf_solve(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    """Solve a @ x = b over GF(256); a is n x n, b is n x size. None if singular."""
    a, b = a.copy(), b.copy()
    n = a.shape[0]
    for col in range(n):
        pivot = next((r for r in range(col, n) if a[r, col]), None)
        if pivot is None:
            return None
        if pivot != col:
            a[[col, pivot]] = a[[pivot, col]]
            b[[col, pivot]] = b[[pivot, col]]
        inv = _inv(int(a[col, col]))
        a[col] = _MUL[inv][a[col]]
        b[col] = _MUL[inv][b[col]]
        for r in range(n):
            factor = int(a[r, col])
            if r != col and factor:
                a[r] ^= _MUL[factor][a[col]]
                b[r] ^= _MUL[factor][b[col]]
    return b


def _encode_blocks(matrix: Matrix, data: np.ndarray) -> np.ndarray:
    coding = np.zeros((matrix.m, data.shape[1]), dtype=np.uint8)
    for i in range(matrix.m):
        for j in range(matrix.k):
            coding[i] ^= _MUL[matrix.rows[i, j]][data[j]]
    return coding


def encode_from_bytes(matrix: Matrix, data: bytes, size: int, packetsize: int = 0) -> bytes:
    """Return the k data blocks followed by the m coding blocks."""
    blocks = np.frombuffer(data.ljust(matrix.k * size, b"\x00"), dtype=np.uint8)
    blocks = blocks[: matrix.k * size].reshape(matrix.k, size)
    return blocks.tobytes() + _encode_blocks(matrix, blocks).tobytes()


def decode_from_bytes(
    matrix: Matrix,
    data: bytes,
    erasures: Iterable[int],
    size: int,
    packetsize: int = 0,
    data_only: bool = False,
) -> bytes:
    """Restore erased blocks; returns b"" when recovery is impossible (as pyjerasure does)."""
    k, m = matrix.k, matrix.m
    erased = sorted(set(erasures))
    if not erased or len(erased) > m or size % 16 or len(data) != (k + m) * size:
        return b""
    blocks = np.frombuffer(data, dtype=np.uint8).reshape(k + m, size).copy()
    survivors = [i for i in range(k + m) if i not in erased][:k]
    if any(i < k for i in erased):
        a = np.zeros((k, k), dtype=np.uint8)
        for row, idx in enumerate(survivors):
            if idx < k:
                a[row, idx] = 1
            else:
                a[row] = matrix.rows[idx - k]
        solved = _gf_solve(a, blocks[survivors])
        if solved is None:
            return b""
        blocks[:k] = solved
    if any(i >= k for i in erased):
        blocks[k:] = _encode_blocks(matrix, blocks[:k])
    out = blocks.tobytes()
    return out[: k * size] if data_only else out
