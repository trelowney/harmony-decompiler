"""Read a region of a connected remote's flash. Read-only.

    python tools/read_flash.py --check
    python tools/read_flash.py 0x820000 64
    python tools/read_flash.py 0x817000 4096 --out region.bin

`--check` reads a few bytes from addresses whose contents are already known
and compares them, which is the only honest way to start: a read routine that
has not been checked against something known is not evidence about anything.

Address map for arch 9 (the 525), from libconcord/remote_info.h:

    0x800000   flash_base          the external serial flash
    0x810000   firmware_base       the stored copy of the firmware
    0x820000   config_base         the config, which is what this repo decodes

Note that a config's own 24-bit pointers are relative to `flash_base`, which
is why `config_base` reads as 0x20000 everywhere else in this repository.

Nothing here writes. hid_query.py refuses write commands outright and this
only adds a read.
"""
import sys
from pathlib import Path

import _paths  # noqa: F401
from hid_query import Remote

KNOWN = [
    ("config", 0x820000, Path(__file__).resolve().parent.parent
     / "samples" / "harmony525" / "config.bin"),
]


def check(rm):
    """Read where we already know the answer, and say whether it matches."""
    ok = True
    for name, addr, path in KNOWN:
        if not path.exists():
            print(f"  {name}: no local copy at {path}, skipping")
            continue
        want = path.read_bytes()[:64]
        got, err = rm.read_flash(addr, 64)
        if err:
            print(f"  {name} @0x{addr:06X}: {err}")
            ok = False
            continue
        same = got == want
        ok &= same
        print(f"  {name} @0x{addr:06X}: {'MATCH' if same else 'DIFFERENT'}")
        print(f"      remote: {got[:32].hex(' ')}")
        print(f"      file:   {want[:32].hex(' ')}")
    return ok


def main(argv):
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2

    out = None
    if "--out" in args:
        i = args.index("--out")
        out = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    rm = Remote()
    try:
        r, note = rm.get_version()
        if not r:
            print(f"no response to GET_VERSION: {note}")
            return 1
        print(f"connected: firmware {r[1] >> 4}.{r[1] & 0xF}, "
              f"architecture {r[5] >> 4}, protocol {r[7]}")

        if "--check" in args:
            print("\n=== reading where the answer is already known ===")
            return 0 if check(rm) else 1

        addr, length = int(args[0], 0), int(args[1], 0)
        print(f"\nreading {length} bytes from 0x{addr:06X}")
        data, err = rm.read_flash(addr, length)
        print(f"  got {len(data)} bytes" + (f", {err}" if err else ""))
        if data:
            blank = data.count(0xFF)
            print(f"  0xFF: {blank} of {len(data)} "
                  f"({100 * blank / len(data):.1f}%)")
            print(f"  first 32: {data[:32].hex(' ')}")
            print(f"  last 32:  {data[-32:].hex(' ')}")
        if out and data:
            out.write_bytes(data)
            print(f"  written to {out}")
    finally:
        rm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
