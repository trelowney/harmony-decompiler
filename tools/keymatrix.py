"""Test the hypothesis that key codes are keyboard-matrix addresses.

Hypothesis: code = 0x80 + row*8 + column, row 0-7, column 0-7.

Checked against our 51 codes and the 53 codes of arch 8. It holds cleanly on
both: every code lands in a distinct cell, no collisions. That is the whole
argument - a wrong decoding of 51 values into a 64-cell grid would be very
unlikely to avoid collisions entirely.

What this does NOT give you is which physical button sits at which cell. See
docs/OPEN-QUESTIONS.md - that is the thing currently blocking everything.

Usage:
    python keymatrix.py
"""
import sys

# The main table of the 525 sample, in file order, and the arch 8 equivalent
# from the samples in the concordance thread. Extracted with keytable.py and
# find_keytables.py respectively; hardcoded here so this script runs standalone.
OURS = [0x89, 0x8B, 0x8A, 0x8D, 0x8C, 0x06, 0x8F, 0x8E, 0x81, 0x83, 0x82,
        0x85, 0x84, 0x87, 0x86, 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F,
        0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0xAB, 0xAA, 0xA9, 0xAF,
        0xAE, 0xAD, 0xAC, 0xA3, 0xA2, 0xA1, 0xA7, 0xA6, 0xA5, 0xA4, 0xB9,
        0xB2, 0xB3, 0xB1, 0xB6, 0xB7, 0xB4, 0xB5]

ARCH8 = [0x88, 0x8B, 0x8A, 0x8D, 0x8C, 0x8F, 0x8E, 0x81, 0x83, 0x82, 0x85,
         0x87, 0x86, 0x98, 0x99, 0x9A, 0x9B, 0x9D, 0x9F, 0x90, 0x91, 0x92,
         0x94, 0x95, 0x96, 0x97, 0xAB, 0xA9, 0xA8, 0xAE, 0xAD, 0xAC, 0xA3,
         0xA2, 0xA1, 0xA0, 0xA6, 0xA5, 0xA4, 0xBA, 0xBB, 0xB8, 0xB9, 0xBE,
         0xBF, 0xBD, 0xB2, 0xB3, 0xB0, 0xB6, 0xB7, 0xB4, 0xB5]


def check(name, codes):
    print(f"\n=== {name}: {len(codes)} codes ===")
    fits = [c for c in codes if 0x80 <= c <= 0xBF]
    out = [c for c in codes if not (0x80 <= c <= 0xBF)]
    print(f"  within 0x80-0xBF: {len(fits)}/{len(codes)}"
          f"   outside: {[hex(c) for c in out] or 'none'}")

    grid = {}
    for c in fits:
        r, col = (c - 0x80) // 8, (c - 0x80) % 8
        grid[(r, col)] = c
    print(f"  distinct matrix cells: {len(grid)} (collisions: "
          f"{len(fits) - len(grid)})")

    print("\n      c0   c1   c2   c3   c4   c5   c6   c7")
    for r in range(8):
        row = f"  r{r}  "
        for col in range(8):
            row += f" {grid[(r,col)]:02X} " if (r, col) in grid else "  . "
        print(row)
    used = {r for r, _ in grid}
    print(f"  rows used: {sorted(used)}")
    cols = {c for _, c in grid}
    print(f"  columns used: {sorted(cols)}")
    return grid


g9 = check("Harmony 525 (arch 9)", OURS)
g8 = check("ARCH 8 (720/785/88x)", ARCH8)

print(f"\n=== MATRIX COMPARISON ===")
only9 = sorted(set(g9) - set(g8))
only8 = sorted(set(g8) - set(g9))
both = sorted(set(g9) & set(g8))
print(f"  in both: {len(both)}   525 only: {len(only9)} {only9}")
print(f"  arch 8 only: {len(only8)} {only8}")
print("\nThe codes outside 0x80-0xBF are not physical keys - the 525's 0x06 is a")
print("virtual event. See FORMAT.md section 5g.")
