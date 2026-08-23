"""Group arch-9 modes by the shape of their physical list, and check paging.

This exists because of one wasted write. A fifth device was added to a 525 and
the new entry was put on a second page of the `Devices` menu. Everything passed:
exact round trip, both checksums, all 200 original infrared records identical,
all 135 original screens pixel-identical, an independent reader happy. The
remote took the write, read back byte for byte, and showed four devices. The
second page was in the file and the firmware never rendered it.

`Devices` is not a paged menu. Its mode carries a one-entry physical list
holding a `0x72` handler, and in the sample **every** mode shaped like that has
exactly one page. Nothing in the file says so; you have to go and count.

So this asks the question that would have caught it, and it is deliberately a
question about the file in front of you rather than a rule baked in here:

    for the shape of mode N's physical list, does any mode in this same config
    have more than one page?

If the answer is no and you have just given mode N a second page, you have
invented something the firmware has never been asked to do. That may still be
right. It is not something to find out on hardware.

See docs/FORMAT.md sections 4o and 5o for the write this came out of,
4q for what the firmware actually does with a page count, and 5p for the
other architectures, where a menu of this shape does page.

Usage:
    python tools/check_525_mode_pages.py [config.bin|config.EZHex]
    python tools/check_525_mode_pages.py new.EZHex --against samples/harmony525/config.bin

Exits non-zero if a mode has more pages than its shape has precedent for.
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

import _paths
from _paths import CONFIG_BASE as BASE, get_blob

MODE_SLOT = 6


def u16(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 2], "little")


def u24(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 3], "little")


def section_offsets(blob: bytes) -> list[int | None]:
    marker = blob.index(b"CMAH")
    raw = [int.from_bytes(blob[p:p + 4], "little") for p in range(0x0C, marker, 4)]
    while raw and raw[-1] == 0:
        raw.pop()
    return [address - BASE if address else None for address in raw]


def tagged(blob: bytes, offset: int) -> list[tuple[int, int, int]]:
    """One narrow/wide tagged list as (tag, opcode, operand) triples."""
    wide = blob[offset] == 0
    count = blob[offset + 1] if wide else blob[offset]
    stride = 5 if wide else 4
    base = offset + (2 if wide else 1)
    out = []
    for k in range(count):
        entry = base + stride * k
        out.append((
            blob[entry + (1 if wide else 0)],
            blob[entry + (4 if wide else 3)],
            u16(blob, entry + (2 if wide else 1)),
        ))
    return out


def modes(blob: bytes):
    """Yield (index, page_count, physical list) for every mode."""
    table = section_offsets(blob)[MODE_SLOT]
    for index in range(u24(blob, table)):
        entry = u24(blob, table + 3 + 3 * index) - BASE
        yield index, u16(blob, entry + 4), tagged(blob, u24(blob, entry + 1) - BASE)


def shape(physical: list[tuple[int, int, int]]) -> str:
    """A mode's kind, as the file itself distinguishes them.

    Not a taxonomy from anywhere else: it is the entry count plus which handler
    opcodes appear, which is the coarsest thing that separates the modes that
    page from the modes that do not.
    """
    if not physical:
        return "empty"
    handlers = sorted({opcode for _tag, opcode, _operand in physical if opcode})
    names = ",".join(f"0x{h:02X}" for h in handlers) or "none"
    return f"{len(physical)} entries [{names}]"


def survey(blob: bytes) -> dict[str, list[tuple[int, int]]]:
    groups: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for index, pages, physical in modes(blob):
        groups[shape(physical)].append((index, pages))
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_BLOB)
    parser.add_argument("--against", type=Path, default=None,
                        help="take the precedent from this config instead, which "
                             "is what you want when checking an edited file "
                             "against the original it was built from")
    args = parser.parse_args()

    blob = get_blob(args.config)
    groups = survey(blob)
    precedent = survey(get_blob(args.against)) if args.against else groups

    print(f"{'shape':<28} {'modes':>5} {'max pages':>9}  which")
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        entries = groups[key]
        most = max(pages for _index, pages in entries)
        listed = ", ".join(str(index) for index, _pages in entries[:8])
        if len(entries) > 8:
            listed += ", ..."
        print(f"{key:<28} {len(entries):>5} {most:>9}  {listed}")

    problems = []
    for key, entries in groups.items():
        known = precedent.get(key)
        limit = max(pages for _index, pages in known) if known else 1
        for index, pages in entries:
            if pages > limit:
                problems.append((index, pages, key, limit, known is not None))

    print()
    if not problems:
        source = args.against.name if args.against else "this config"
        print(f"PASS every mode's page count has precedent in {source}")
        return 0

    for index, pages, key, limit, seen in sorted(problems):
        where = "the reference config" if args.against else "this config"
        known = f"the most any mode of that shape has in {where} is {limit}"
        if not seen:
            known = f"no mode of that shape exists in {where} at all"
        print(f"FAIL mode {index} has {pages} pages; its shape is {key} and {known}")
    print()
    print("A mode with more pages than its shape has precedent for is not")
    print("necessarily wrong, but the firmware has never been asked to render")
    print("one. Do not settle it on hardware. See docs/FORMAT.md section 5o.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
