"""Check that a name table with non-ASCII names survives the round trip.

A French Harmony 720 from issue #44 names its devices "Récepteur AV" and
"Magnétoscope numérique", and the byte for é is 0xE9. The name table reader
used to decode names as ASCII with replacement, so that byte became U+FFFD on
the way in and the compiler then refused to encode it on the way out. Names are
bytes; they are carried as Latin-1 now, which maps all 256 values one to one.

This builds a table holding every byte from 0x20 to 0xFF in a name, parses it,
emits it and requires the same bytes back. The negative check re-runs the old
decoding on the same record and requires it to lose information, so the test
would notice if the fixture stopped exercising the fault.

Usage:
    python tools/verify_name_encoding.py
"""
from __future__ import annotations

import sys

import _paths  # noqa: F401
import hconfig


def build_table(names: list[bytes]) -> bytes:
    body = bytearray()
    for i, name in enumerate(names):
        body += bytes([hconfig.NAME_REC])
        body += (len(name) + 4).to_bytes(2, "little")
        body += (0).to_bytes(2, "little")       # parent
        body += (i + 1).to_bytes(2, "little")   # index
        body += name
    declared = 5 + len(body)
    return (hconfig.NAME_MAGIC + declared.to_bytes(2, "little") + b"\x00"
            + bytes(body) + hconfig.NAME_END)


def main() -> int:
    names = [
        "Récepteur_AV_Power_2".encode("latin-1"),
        "Magnétoscope_numérique_Input_2".encode("latin-1"),
        bytes(range(0x20, 0x100)),
        b"plain_ascii_1",
    ]
    blob = build_table(names)

    region = hconfig.parse_name_table(blob, 0, len(blob))
    if region is None:
        print("FAIL  the fixture does not parse as a name table")
        return 1

    rebuilt = hconfig.emit_region(region)
    if rebuilt != blob:
        print(f"FAIL  {len(blob)} bytes in, {len(rebuilt)} out, not identical")
        return 1
    print(f"PASS  {len(names)} names, every byte 0x20-0xFF, "
          f"{len(blob)} bytes identical after parse and emit")

    lossy = names[2].decode("ascii", "replace").encode("ascii", "replace")
    if lossy == names[2]:
        print("FAIL  negative: the old decoding keeps these bytes, so this "
              "fixture no longer tests the fault")
        return 1
    print("PASS  negative: the old ASCII decoding loses "
          f"{sum(a != b for a, b in zip(lossy, names[2]))} of "
          f"{len(names[2])} bytes of the same name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
