#!/usr/bin/env python3
"""Verify kkong42's H880/H885 screen inventories against the public configs.

This deliberately keeps two independent views:

* this repository's ``hconfig`` reader proves the device/activity counts from
  the IR-group table and the named activity-state variable;
* Danny Bloemendaal's MIT-licensed ``harmony-explorations`` screen reader
  supplies the rendered menu programs. It is **required**, not optional:
  pass ``--explorations`` at a checkout of it. An earlier version of this
  sentence called it optional, which the code never was.

The screen assertions are human oracles, not format inference.  The expected
labels were written down by @kkong42 from the real remotes in
trelowney/harmony-decompiler issues #18 and #20.  Glyph maps are sample-local
because Harmony stores glyph indices rather than characters.

There is one deliberate distinction.  Issue #20 numbers the H885 devices in
logical device order, while the decoded screen program places them visually in
the order 1, 5, 2, 3, 4, 6, 7.  The verifier therefore checks that the exact
human-reported label set is present, but does not misrepresent those derived
coordinates as a human-confirmed layout.  Activity pages and the H880 device
page do agree with the reported row-major layout and are checked as such.

Nothing here talks to a remote or writes an input file.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import hconfig


@dataclass(frozen=True)
class ScreenOracle:
    sample: str
    devices: int
    activities: int
    device_program: int
    activity_programs: tuple[int, ...]
    glyphs: dict[int, str]
    reported_devices: tuple[str, ...]
    decoded_device_pages: tuple[tuple[str | None, ...], ...]
    device_layout_is_human_confirmed: bool
    activity_pages: tuple[tuple[str | None, ...], ...]


def _glyph_map(pairs: dict[str, int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for char, code in pairs.items():
        if code in out and out[code] != char:
            raise AssertionError(f"glyph {code} assigned to both {out[code]!r} and {char!r}")
        out[code] = char
    return out


ORACLES = (
    ScreenOracle(
        sample="H880-Bedroom.EZHex",
        devices=4,
        activities=4,
        device_program=0x035839,
        activity_programs=(0x0355F7,),
        glyphs=_glyph_map({
            " ": 24, "-": 41, "0": 13, "2": 4, "3": 6, "5": 9, "6": 8,
            "A": 14, "B": 20, "C": 45, "D": 49, "G": 50, "O": 17,
            "P": 16, "R": 58, "S": 44, "T": 48, "V": 62, "Y": 65,
            "a": 19, "c": 30, "e": 22, "g": 35, "h": 31, "i": 33,
            "l": 25, "m": 32, "n": 34, "o": 27, "p": 53, "s": 36,
            "t": 21, "u": 52, "v": 26, "w": 28, "x": 63, "y": 2,
        }),
        reported_devices=(
            "Pana 32AS500", "Pana 32AS600", "Dune Real Box", "DTR-T2200",
        ),
        decoded_device_pages=((
            "Pana 32AS500", "Pana 32AS600", "Dune Real Box", "DTR-T2200",
            None, None, None, None,
        ),),
        device_layout_is_human_confirmed=True,
        activity_pages=((
            "TV", "Dune", "Google TV", "YouView",
            "System Options", None, None, None,
        ),),
    ),
    ScreenOracle(
        sample="H885-LivingRoom.EZHex",
        devices=7,
        activities=9,
        device_program=0x04D635,
        activity_programs=(0x04CE23, 0x04CECD),
        glyphs=_glyph_map({
            " ": 21, "-": 39, "/": 68, "0": 3, "1": 5, "2": 6, "4": 8,
            "5": 9, "6": 10, "8": 12, "9": 13, "A": 57, "B": 17,
            "C": 43, "D": 47, "F": 15, "G": 48, "H": 1, "K": 69,
            "L": 54, "M": 58, "N": 44, "O": 14, "P": 27, "R": 56,
            "S": 42, "T": 46, "V": 62, "X": 65, "Y": 64, "Z": 67,
            "a": 16, "c": 28, "e": 19, "g": 33, "h": 29, "i": 31,
            "l": 22, "m": 30, "n": 32, "o": 24, "p": 51, "r": 20,
            "s": 34, "t": 18, "u": 50, "v": 23, "w": 25, "x": 66,
            "y": 2,
        }),
        reported_devices=(
            "TX-55GZ950B", "HR-S6855", "DMP-BD60", "DTR-T2110",
            "TX-NR626", "RealBox 4K", "DVL-909",
        ),
        decoded_device_pages=((
            "TX-55GZ950B", "TX-NR626", "HR-S6855", "DMP-BD60",
            "DTR-T2110", "RealBox 4K", "DVL-909", None,
        ),),
        device_layout_is_human_confirmed=False,
        activity_pages=(
            ("TV", "Dune", "Google TV", "Blu-ray", "CD", "YouView", "VCR", "LaserDisc"),
            ("FM/AM", "System Options", None, None, None, None, None, None),
        ),
    ),
)


def _regions(model: dict) -> list[dict]:
    return model["blob"]["regions"]


def _structural_inventory(path: Path) -> tuple[int, int]:
    """Return (devices, activities) using only this repository's parser."""
    model = hconfig.decompile(path.read_bytes(), path.name)
    regions = _regions(model)

    ir_tables = [r for r in regions if r.get("kind") == "pointer_table" and r.get("section") == 5]
    if len(ir_tables) != 1:
        raise AssertionError(f"{path.name}: expected one section-5 IR-group table, got {len(ir_tables)}")
    devices = len(ir_tables[0]["targets"])

    names = [r for r in regions if r.get("kind") == "name_table" and r.get("section") == 0]
    if len(names) != 1:
        raise AssertionError(f"{path.name}: expected one section-0 name table, got {len(names)}")
    activity_names = [
        record["name"] for record in names[0]["records"]
        if record["name"].startswith("CurrentActivityState_")
    ]
    if len(activity_names) != 1:
        raise AssertionError(f"{path.name}: expected one activity-state name, got {activity_names}")
    match = re.search(r"_(\d+)$", activity_names[0])
    if match is None:
        raise AssertionError(f"{path.name}: activity-state name has no value count")
    activities = int(match.group(1)) - 1
    return devices, activities


def _load_upstream(explorations: Path):
    tools = explorations / "tools"
    if not (tools / "_bootstrap.py").is_file():
        raise SystemExit(f"harmony-explorations tools not found at {tools}")
    sys.path.insert(0, str(tools))
    import _bootstrap  # noqa: F401, PLC0415
    from harmony import ezfile, gspm  # noqa: PLC0415
    return ezfile, gspm


def _decode(codes, glyphs: dict[int, str], context: str) -> str:
    missing = sorted(set(codes) - glyphs.keys())
    if missing:
        raise AssertionError(f"{context}: unmapped glyph codes {missing}")
    return "".join(glyphs[code] for code in codes)


def _external_codes(container, operands: bytes) -> bytes:
    address = int.from_bytes(operands[2:5], "little")
    offset = container.blob_offset_of(address)
    if offset is None:
        raise AssertionError(f"external string 0x{address:06X} is outside the config")
    end = container.blob.find(b"\x00", offset)
    if end < 0:
        raise AssertionError(f"external string 0x{address:06X} is not terminated")
    return container.blob[offset:end]


def _program_strings(container, program, gspm, glyphs: dict[int, str]):
    out = []
    font = None
    for instruction in program:
        if instruction.opcode == gspm.SCREEN_SELECT_FONT and instruction.operands:
            font = instruction.operands[0]
        codes = None
        if instruction.opcode == gspm.SCREEN_TEXT_INLINE and instruction.glyphs:
            codes = instruction.glyphs
        elif instruction.opcode == 4:
            codes = _external_codes(container, instruction.operands)
        if codes is not None:
            position = tuple(instruction.operands[:2])
            text = _decode(codes, glyphs, f"string at {position}")
            out.append((position[0], position[1], font, text))
    return out


def _closure(programs, root: int):
    pending = [root]
    seen = set()
    out = []
    while pending:
        address = pending.pop()
        if address in seen:
            continue
        seen.add(address)
        program = programs.get(address)
        if program is None:
            raise AssertionError(f"screen program 0x{address:06X} did not decode")
        out.append(program)
        for instruction in program:
            pending.extend(target for target in instruction.targets if target)
    return out


def _join(parts: list[str]) -> str:
    text = ""
    for part in parts:
        if not text or text.endswith("-"):
            text += part
        else:
            text += " " + part
    return text


def _menu_page(strings) -> tuple[str | None, ...]:
    """Turn 128x160 menu fragments into the eight left/right row cells."""
    cells: list[list[tuple[int, str]]] = [[] for _ in range(8)]
    for x, y, _font, text in strings:
        if y == 10:  # page title
            continue
        if y >= 145:  # page-number footer
            continue
        row = min(3, max(0, (y - 25) // 32))
        column = 0 if x < 64 else 1
        cells[2 * row + column].append((y, text))
    return tuple(_join([text for _y, text in sorted(parts)]) if parts else None for parts in cells)


def _screen_inventory(path: Path, oracle: ScreenOracle, ezfile, gspm):
    payload = ezfile.decode_payload(ezfile.load_image(path)).payload
    container = gspm.parse(payload)
    programs, failed = container.reachable_screen_programs()
    if failed:
        raise AssertionError(f"{path.name}: {len(failed)} screen programs did not decode")

    device_strings = _program_strings(
        container, programs[oracle.device_program], gspm, oracle.glyphs,
    )
    device_titles = [text for x, y, _font, text in device_strings if (x, y) == (3, 10)]
    if device_titles != ["Devices"]:
        raise AssertionError(f"{path.name}: device title is {device_titles}")
    device_pages = (_menu_page(device_strings),)

    activity_pages = []
    activity_titles = set()
    for root in oracle.activity_programs:
        strings = []
        for program in _closure(programs, root):
            strings.extend(_program_strings(container, program, gspm, oracle.glyphs))
        activity_titles.update(text for x, y, _font, text in strings if (x, y) == (3, 10))
        activity_pages.append(_menu_page(strings))
    if activity_titles != {"Choose an Activity"}:
        raise AssertionError(f"{path.name}: activity title is {sorted(activity_titles)}")
    return device_pages, tuple(activity_pages)


def verify(samples: Path, explorations: Path) -> None:
    ezfile, gspm = _load_upstream(explorations)
    for oracle in ORACLES:
        path = samples / oracle.sample
        if not path.is_file():
            raise AssertionError(f"missing public sample {path}")

        devices, activities = _structural_inventory(path)
        assert devices == oracle.devices, (path.name, "devices", devices, oracle.devices)
        assert activities == oracle.activities, (path.name, "activities", activities, oracle.activities)

        device_pages, activity_pages = _screen_inventory(path, oracle, ezfile, gspm)
        assert device_pages == oracle.decoded_device_pages, (
            path.name, "decoded device pages", device_pages,
        )
        decoded_devices = tuple(
            label for page in device_pages for label in page if label is not None
        )
        assert sorted(decoded_devices) == sorted(oracle.reported_devices), (
            path.name, "device labels", decoded_devices, oracle.reported_devices,
        )
        assert activity_pages == oracle.activity_pages, (path.name, "activity pages", activity_pages)

        device_claim = (
            "device layout"
            if oracle.device_layout_is_human_confirmed
            else "device label set (decoded visual order intentionally differs from reported numbering)"
        )
        print(
            f"PASS {path.name}: {devices} devices, {activities} activities; "
            f"{device_claim} and activity layout match @kkong42's human oracle"
        )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples", type=Path, default=root / "samples" / "arch8",
        help="directory holding the public H880/H885 samples",
    )
    parser.add_argument(
        "--explorations", type=Path,
        default=None,
        help="a checkout of Danny Bloemendaal's harmony-explorations "
             "(github.com/dannybloe/harmony-explorations, MIT). Its parser is "
             "required, not optional: without it this check cannot run",
    )
    args = parser.parse_args()
    if args.explorations is None:
        raise SystemExit(
            "--explorations is required: this check reads a parser that is not "
            "in this repository. See --help.")
    verify(args.samples, args.explorations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
