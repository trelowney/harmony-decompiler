"""Hexdump selected regions of the binary config.

Usage:
    python hexdump.py                       # the three regions of interest
    python hexdump.py 0xF340 0x180          # an arbitrary region
    python hexdump.py 0xF340 0x180 other.bin
"""
import sys

from _paths import get_blob, hexdump

REGIONS = [
    (0x000000, 0x100, "START - header / cookie"),
    (0x00F340, 0x180, "NAME REGION - HarmonyAssistant state variables"),
    (0x013250, 0x50, "END of file"),
]


def main(argv):
    if len(argv) >= 2:
        off, length = int(argv[0], 0), int(argv[1], 0)
        data = get_blob(argv[2] if len(argv) > 2 else None)
        hexdump(data, off, length, f"0x{off:06X}")
    else:
        data = get_blob(argv[0] if argv else None)
        for off, ln, label in REGIONS:
            hexdump(data, off, ln, label)

    print(f"\nblob length : {len(data)} B (0x{len(data):X})")
    print(f"first 4 B   : {data[:4]!r}")
    print(f"last 4 B    : {data[-4:]!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
