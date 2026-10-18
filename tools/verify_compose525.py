"""Check compose525 on the public 525 sample, offline.

Adds devices one after another and requires: the first goes top left on a new
Devices page 2 shared by every Devices screen, the next ones fill the other
corners in reading order, the fifth opens page 3, every Devices page carries
"n OF m", and each step passes add_device's own checks (records decode as
asked, keys and soft keys send them, nothing else changed, the pool reader
accounts for every page, the file round-trips).

    python verify_compose525.py [--out DIR]   DIR receives the composed files
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _paths
import compose525 as c
import hconfig
import render_525_screens as renderer
import verify_525_semantics as sem
from clone_525_device import mode_fields


def device(n: int):
    address = 0x40 + n
    names = ["Up", "Down", "Left", "Right", "OK", "Exit", "Power"]
    commands = [c.nec_command(k, address, 0x10 + i, repeat=k in ("Up", "Down", "Left", "Right"))
                for i, k in enumerate(names)]
    keys = {k: k for k in names if k in c.KEYS}
    lcd = [(("Power", "Power"), ("Ok", "OK"), None, ("Exit", "Exit"))]
    return commands, keys, lcd


def counter_text(blob, root) -> str:
    out = []
    for item in sem.screen_program_path(blob, root):
        if item["opcode"] in (4, 5) and item["operands"][1] == 56:
            out.append(item["operands"][2] if item["opcode"] == 5 else None)
    return " ".join("?" if x is None else {v: str(k) for k, v in c.COUNTER_DIGIT.items()}.get(x, "OF")
                    for x in out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw = Path(_paths.SAMPLE_CONTAINER if hasattr(_paths, "SAMPLE_CONTAINER")
               else Path(__file__).resolve().parents[1] / "samples" / "harmony525" / "config.EZHex").read_bytes()
    expected = [(2, 0), (2, 1), (2, 2), (2, 3), (3, 0)]
    for n, (page, corner) in enumerate(expected):
        commands, keys, lcd = device(n)
        raw, placed = c.add_device(raw, f"Box {'ABCDE'[n]}", commands, keys, lcd)
        got = (placed.devices_page + 1, placed.corner)
        print(f"  Box {'ABCDE'[n]}: group {placed.group}, mode {placed.mode}, "
              f"Devices page {got[0]}, corner {got[1]}")
        if got != (page, corner):
            print(f"FAIL expected page {page} corner {corner}")
            return 1
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"compose-{n + 1}.EZHex").write_bytes(raw)
    blob = hconfig.split_container(raw)[2]
    S = sem.section_offsets(blob)
    d = c._Doc(raw)
    for m in d.devices_modes():
        texts = [counter_text(blob, p["program"]) for p in mode_fields(blob, S, m)["pages"]]
        if texts != ["1 OF 3", "2 OF 3", "3 OF 3"]:
            print(f"FAIL Devices mode {m} counters {texts}")
            return 1
    print("PASS five devices composed onto the public sample, pages and counters as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
