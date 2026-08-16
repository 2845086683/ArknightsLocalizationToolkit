# Copyright (c) 2022-2026, Harry Huang
# BSD 3-Clause License
# Adapted from isHarryh/Ark-Unpacker src/lz4ak/Block.py.
from __future__ import annotations

from typing import Union

import lz4.block


ByteString = Union[bytes, bytearray, memoryview]


def _read_extra_length(data: ByteString, position: int, maximum: int) -> tuple[int, int]:
    length = 0
    while position < maximum:
        value = data[position]
        length += value
        position += 1
        if value != 0xFF:
            break
    return length, position


def decompress_lz4ak(compressed_data: ByteString, uncompressed_size: int) -> bytes:
    """Decode the token/offset-swapped LZ4 variant used by recent Arknights bundles."""
    input_position = 0
    output_position = 0
    fixed = bytearray(compressed_data)
    compressed_size = len(compressed_data)

    while input_position < compressed_size:
        literal_length = fixed[input_position] & 0xF
        match_length = (fixed[input_position] >> 4) & 0xF
        fixed[input_position] = (literal_length << 4) | match_length
        input_position += 1

        if literal_length == 0xF:
            extra, input_position = _read_extra_length(fixed, input_position, compressed_size)
            literal_length += extra
        input_position += literal_length
        output_position += literal_length
        if output_position >= uncompressed_size:
            break

        offset = (fixed[input_position] << 8) | fixed[input_position + 1]
        fixed[input_position] = offset & 0xFF
        fixed[input_position + 1] = (offset >> 8) & 0xFF
        input_position += 2
        if match_length == 0xF:
            extra, input_position = _read_extra_length(fixed, input_position, compressed_size)
            match_length += extra
        output_position += match_length + 4

    return lz4.block.decompress(fixed, uncompressed_size)

