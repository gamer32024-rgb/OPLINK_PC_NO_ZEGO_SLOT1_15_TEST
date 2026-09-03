#!/usr/bin/env python3
"""Convert a raw RP2040 flash binary to UF2.

This intentionally implements only the small UF2 subset needed for Phase 0
Pico/Pico H flashing via BOOTSEL mass storage.
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path


UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID_PRESENT = 0x00002000
RP2040_FAMILY_ID = 0xE48BFF56
RP2040_FLASH_BASE = 0x10000000
UF2_BLOCK_SIZE = 512
UF2_PAYLOAD_SIZE = 256


def convert_bin_to_uf2(input_path: Path, output_path: Path) -> None:
    data = input_path.read_bytes()
    if not data:
        raise ValueError(f"Input binary is empty: {input_path}")

    block_count = math.ceil(len(data) / UF2_PAYLOAD_SIZE)
    blocks = []

    for block_no in range(block_count):
        offset = block_no * UF2_PAYLOAD_SIZE
        payload = data[offset : offset + UF2_PAYLOAD_SIZE]
        payload = payload.ljust(UF2_PAYLOAD_SIZE, b"\x00")

        header = struct.pack(
            "<IIIIIIII",
            UF2_MAGIC_START0,
            UF2_MAGIC_START1,
            UF2_FLAG_FAMILY_ID_PRESENT,
            RP2040_FLASH_BASE + offset,
            UF2_PAYLOAD_SIZE,
            block_no,
            block_count,
            RP2040_FAMILY_ID,
        )

        block = (
            header
            + payload
            + bytes(UF2_BLOCK_SIZE - len(header) - UF2_PAYLOAD_SIZE - 4)
            + struct.pack("<I", UF2_MAGIC_END)
        )
        if len(block) != UF2_BLOCK_SIZE:
            raise AssertionError(f"Unexpected UF2 block length: {len(block)}")
        blocks.append(block)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"".join(blocks))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Input .bin file")
    parser.add_argument("output", type=Path, help="Output .uf2 file")
    args = parser.parse_args()

    convert_bin_to_uf2(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
