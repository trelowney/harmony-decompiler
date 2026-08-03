"""Find out which address spaces (kinds) the remote actually returns data for.

kind: 00 EEPROM, 01 STATE, 06 RAM, 07 REGISTER

Result on the 525: only STATE returns anything, and only in word mode. Byte mode
returns zeros everywhere, consistent with arch >= 8 using the word variants.
See FORMAT.md §5d.

Read only - same whitelist as hid_query.py.

Usage:
    python probe_kinds.py
"""
import sys

from hid_query import Remote

KINDS = {0x00: "EEPROM", 0x01: "STATE", 0x06: "RAM", 0x07: "REGISTER"}
N = 32


def main():
    rm = Remote()
    try:
        for kind, name in KINDS.items():
            print(f"\n=== kind 0x{kind:02X} {name} ===")
            for mode, fn in (("byte", rm.read_misc_byte),
                             ("word", rm.read_misc_word)):
                vals, fails = [], 0
                for a in range(N):
                    v = fn(a, kind)
                    if v is None:
                        fails += 1
                        vals.append(None)
                    else:
                        vals.append(v)
                ok = [v for v in vals if v is not None]
                if not ok:
                    print(f"  {mode}: no response ({fails}/{N})")
                    continue
                nz = {i: v for i, v in enumerate(vals) if v}
                print(f"  {mode}: {len(ok)}/{N} responded, "
                      f"{len(nz)} non-zero")
                print(f"    {[('-' if v is None else v) for v in vals]}")
                if mode == "byte" and len(ok) == N:
                    raw = bytes(v for v in vals if v is not None)
                    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
                    print(f"    as text: |{txt}|")
    finally:
        rm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
