#!/usr/bin/env python3
"""Join the measured H880/H885 keypad matrix to the scan codes in the configs.

Three evidence layers, kept apart on purpose:

1. **The board.** @kkong42 opened an 880 and an 885 that had been robbed for
   parts and buzzed out both pads of every key position, discussion 6 comments
   `17981708` and `17992392`. His two local names are a letter A to D and a
   numbered net 1 to 16. That measurement is his and it is the only reason any
   of the rest works.

2. **The configs.** `samples/arch8/H880-Bedroom.EZHex` binds 53 distinct scan
   codes and `samples/arch8/H885-LivingRoom.EZHex` binds 55. Both are in this
   repository, so layers 1 and 2 alone reproduce everything this script prints.

3. **The firmware**, optional. Public H880/H885 images carry a `1..4` selector
   reading `PORTB<4:7>` and a combiner computing `(line - 1) * 4 + input`. The
   images are not in this repository and never will be, so the check runs only
   if you point `--firmware-dir` at your own copies and is skipped otherwise.

What comes out, and the distinction matters:

  * The board is 4 inputs by 16 lines, 63 usable positions. Not 8 by 8, and not
    the 8 by 7 the Harmony 525 uses. See `docs/FORMAT.md` 5g and 5n.

  * Occupancy alone leaves 11,520 electrical relabellings, so most of the map is
    not proved by these two layers.

  * Two things *are* forced by them, with nothing chosen. Only 2 of the 24
    letter assignments survive, and both put C on input 3 and D on input 4;
    net 5 can only be line 5 and net 15 only line 15. So K19 -> 19 and
    K60 -> 60 hold in every surviving relabelling. Those are the 885's two extra
    keys and the two extra codes, the green and the yellow.

  * Agreement with Logitech's own K numbering picks exactly one of the 11,520
    and names 49 of the 55 populated positions outright. That is a design
    argument, not a traced wire, and it is reported separately for that reason.

Nothing here opens USB, writes a sample, or talks to a remote.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _paths import get_blob

LETTERS = "ABCD"

# @kkong42's measurement, discussion 6 comment 17992392, transcribed as
# net -> {pad letter: K number}. K19 and K60 are populated on the 885 only.
MATRIX: dict[int, dict[str, int]] = {
    1: {"A": 1, "B": 2, "C": 3},
    2: {"A": 5, "B": 6, "C": 7, "D": 8},
    3: {"B": 10, "C": 11, "D": 12},
    4: {"A": 13, "B": 14, "C": 15, "D": 16},
    5: {"A": 17, "B": 18, "C": 19, "D": 20},
    6: {"A": 21, "B": 22, "C": 23, "D": 24},
    7: {"A": 25, "B": 26, "C": 27},
    8: {"A": 29, "C": 31, "D": 32},
    9: {"A": 33, "B": 34, "C": 35, "D": 36},
    10: {"A": 37, "B": 38, "D": 40},
    11: {"A": 41, "C": 43, "D": 44},
    12: {"A": 45, "B": 46, "D": 48},
    13: {"B": 50, "C": 55, "D": 56},
    14: {"A": 51, "B": 52, "C": 53, "D": 54},
    15: {"A": 57, "B": 58, "C": 59, "D": 60},
    16: {"A": 61, "B": 62, "C": 63},
}

H885_ONLY = {19, 60}

# Printed legends, @kkong42's transcription of the silkscreen and the case,
# discussion 6 comment 17979094.
LABELS = {
    1: "Activities", 2: "Power", 3: "Help",
    5: "Custom 1, top left", 6: "Custom 3, second left",
    7: "Custom 5, third left", 8: "Custom 7, bottom left",
    10: "Device", 11: "Mute", 12: "screen arrow left",
    13: "Volume +", 14: "D-pad left", 15: "Volume -", 16: "D-pad up",
    17: "Menu", 18: "Exit", 19: "Green, 885 only",
    20: "screen arrow down, Red on the 885", 21: "Stop", 22: "Record",
    23: "Rewind", 24: "Replay", 25: "1", 26: "4", 27: "8",
    29: "7", 31: "Clear", 32: "0", 33: "D-pad OK",
    34: "D-pad down", 35: "D-pad right", 36: "Channel -",
    37: "Channel +", 38: "Media", 40: "Previous channel", 41: "Glow",
    43: "screen arrow right", 44: "Custom 8, bottom right",
    45: "Custom 2, top right", 46: "Custom 4, second right",
    48: "Custom 6, third right", 50: "Guide", 51: "Skip",
    52: "Forward", 53: "Pause", 54: "Play", 55: "Info",
    56: "screen arrow up, Blue on the 885", 57: "2", 58: "3",
    59: "6", 60: "Yellow, 885 only", 61: "5", 62: "9", 63: "Enter",
}


def table_codes(path: Path, offset: int) -> list[int]:
    """Read a key table: 4-byte records <code> <u16 target> <0x7F>."""
    blob = get_blob(str(path))
    codes = []
    while offset + 4 <= len(blob) and blob[offset + 3] == 0x7F:
        codes.append(blob[offset])
        offset += 4
    assert codes, f"no key table at 0x{offset:06X} in {path.name}"
    assert all(code & 0xC0 == 0x80 for code in codes), "a code is not a press event"
    assert len(codes) == len(set(codes)), "duplicate code in the table"
    return [code & 0x3F for code in codes]


def surviving_relabellings(present880: set[int], present885: set[int]):
    """Every (letter -> input, net -> line) pair reproducing both code sets.

    A line's signature is which of its four inputs are occupied, on each of the
    two models. A net can be a line only if its signature matches on both, which
    is what makes the 885's two extra keys carry information.
    """
    need = {}
    for line in range(1, 17):
        base = (line - 1) * 4
        need[line] = (
            frozenset(code - base for code in present880 if base < code <= base + 4),
            frozenset(code - base for code in present885 if base < code <= base + 4),
        )

    results = []
    for order in itertools.permutations(range(1, 5)):
        to_input = dict(zip(LETTERS, order))
        have = {}
        for net, cells in MATRIX.items():
            have[net] = (
                frozenset(to_input[l] for l, k in cells.items() if k not in H885_ONLY),
                frozenset(to_input[l] for l in cells),
            )
        options = {net: [l for l in range(1, 17) if need[l] == have[net]] for net in MATRIX}

        assignments: list[dict[int, int]] = []

        def walk(nets: list[int], used: frozenset[int], acc: dict[int, int]) -> None:
            if not nets:
                assignments.append(dict(acc))
                return
            net = nets[0]
            for line in options[net]:
                if line not in used:
                    acc[net] = line
                    walk(nets[1:], used | {line}, acc)
                    del acc[net]

        walk(sorted(MATRIX), frozenset(), {})
        for net_to_line in assignments:
            results.append((to_input, net_to_line))
    return results


def scan_of(key: int, to_input: dict[str, int], net_to_line: dict[int, int]) -> int:
    for net, cells in MATRIX.items():
        for letter, k in cells.items():
            if k == key:
                return (net_to_line[net] - 1) * 4 + to_input[letter]
    raise KeyError(key)


def firmware_check(directory: Path) -> list[str]:
    """Find the selector and the combiner without assuming where the vars live.

    Searching for literal bytes finds the safe-mode copies only. The application
    images hold the same routine with its two variables at different offsets, so
    the combiner is matched as a template with the two file numbers as holes.
    """
    selector = bytes.fromhex("81a8010c81aa020c81ac030c81ae040c000c")

    def combiner_at(image: bytes, off: int):
        window = image[off:off + 22]
        if len(window) < 22:
            return None
        x, y = window[0], window[2]
        if x == y:
            return None
        want = bytes([x, 0x07, y, 0x07, 0x04, 0x0E, y, 0x03, 0xF3, 0xCF,
                      y, 0xF2, x, 0x51, y, 0x27, y, 0x2B, y, 0x51, 0x12, 0x00])
        return (x, y) if window == want else None

    lines = []
    for name in ("H880-safemode", "H885-safemode", "H880-firmware", "H885-firmware"):
        path = directory / name / f"{name}.bin"
        if not path.is_file():
            lines.append(f"  {name:<16} not present, skipped")
            continue
        image = path.read_bytes()
        sel = [i for i in range(len(image)) if image.startswith(selector, i)]
        comb = [(o, v) for o in range(0, len(image) - 22, 2) if (v := combiner_at(image, o))]
        assert len(sel) == 1, f"{name}: expected one selector, found {len(sel)}"
        assert len(comb) == 1, f"{name}: expected one combiner, found {len(comb)}"
        off, (x, y) = comb[0]
        lines.append(
            f"  {name:<16} selector 0x{sel[0]:04X}   combiner 0x{off:04X}"
            f"   vars 0x{x:02X}/0x{y:02X}"
        )
    return lines


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=Path, default=root / "samples" / "arch8")
    parser.add_argument("--firmware-dir", type=Path, default=None,
                        help="your own copies of the public 880/885 images; "
                             "not in this repository")
    args = parser.parse_args()

    present880 = set(table_codes(args.samples / "H880-Bedroom.EZHex", 0x000A22))
    present885 = set(table_codes(args.samples / "H885-LivingRoom.EZHex", 0x000A32))

    measured885 = {k for cells in MATRIX.values() for k in cells.values()}
    measured880 = measured885 - H885_ONLY
    assert len(present880) == len(measured880) == 53
    assert len(present885) == len(measured885) == 55
    assert present885 - present880 == H885_ONLY

    print(f"configs   880 binds {len(present880)} codes, 885 binds {len(present885)}, "
          f"the two extra are {sorted(H885_ONLY)}")
    print(f"board     {len(measured885)} populated of 4 x 16, "
          f"{sorted(set(range(1, 64)) - measured885)} unpopulated")

    print("\nfirmware")
    if args.firmware_dir is None:
        print("  no --firmware-dir given, skipped. The images are deliberately")
        print("  not in this repository; see the module docstring.")
    else:
        for line in firmware_check(args.firmware_dir):
            print(line)

    solutions = surviving_relabellings(present880, present885)
    print(f"\nrelabellings reproducing both code sets: {len(solutions)}")

    forced_letters = {l: sorted({to_input[l] for to_input, _ in solutions}) for l in LETTERS}
    forced_nets = {n: sorted({m[n] for _, m in solutions}) for n in sorted(MATRIX)}
    print("  pad letter -> input :",
          ", ".join(f"{l}={v[0]}" if len(v) == 1 else f"{l}={v}" for l, v in forced_letters.items()))
    print("  nets pinned to one line:",
          {n: v[0] for n, v in forced_nets.items() if len(v) == 1} or "none")

    pinned = sorted(k for k in measured885
                    if len({scan_of(k, ti, nl) for ti, nl in solutions}) == 1)
    print(f"  keys with the same scan code in every relabelling: "
          f"{['K%d' % k for k in pinned]}")
    for key in pinned:
        code = scan_of(key, *solutions[0])
        print(f"    K{key} -> {code}   {LABELS[key]}")

    best = max(solutions, key=lambda s: sum(scan_of(k, *s) == k for k in measured885))
    score = sum(scan_of(k, *best) == k for k in measured885)
    others = [s for s in solutions
              if sum(scan_of(k, *s) == k for k in measured885) == score]
    assert len(others) == 1, "the designator optimum is not unique"
    print(f"\ndesignator agreement picks one of the {len(solutions)}, "
          f"matching {score} of {len(measured885)} K numbers")

    mapping = {scan_of(k, *best): k for k in measured885}
    print("\nscan  PCB   printed button")
    for scan in range(1, 64):
        key = mapping.get(scan)
        if key is None:
            print(f"{scan:>4}  --    not populated")
        else:
            print(f"{scan:>4}  K{key:<4} {LABELS[key]}")

    exceptions = sorted((k, scan_of(k, *best)) for k in measured885 if scan_of(k, *best) != k)
    print("\nthe six positions where the K number and the scan code differ, which are")
    print("the non-sequential net 13/14 routing @kkong42 called out himself:")
    print("  " + ", ".join(f"K{k} -> {s}" for k, s in exceptions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
