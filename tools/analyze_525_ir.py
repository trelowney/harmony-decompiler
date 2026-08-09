"""Identify structural IR families and likely learned overrides in the public 525.

This is a read-only, dependency-free analysis. It expands architecture-9 class-5
symbol dictionaries, decodes NEC frames where possible, and correlates an IR
group's command records with a mode's physical-key and LCD-page bindings.

Usage:
    python tools/analyze_525_ir.py
    python tools/analyze_525_ir.py --group 3 --mode 111
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

import _paths
import verify_525_semantics as semantics

BASE = semantics.CONFIG_BASE
IR_CLASS_525 = 5


def u16(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 2], "little")


def u24(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 3], "little")


def ir_groups(blob: bytes, sections: list[int | None]):
    table = sections[semantics.IR_SLOT]
    assert table is not None
    result = []
    for group in range(blob[table]):
        start = u24(blob, table + 1 + 3 * group) - BASE
        assert blob[start] == 0
        result.append([u24(blob, start + 3 + 3 * command)
                       for command in range(u16(blob, start + 1))])
    return result


def record(blob: bytes, address: int) -> dict:
    """Read the common IR header; its pointer lands seven bytes into it."""
    start = address - BASE - 7
    assert blob[start] == 0 and blob[start + 7] == IR_CLASS_525
    assert u24(blob, start + 8) == address - 7
    pointer_groups = blob[start + 11]
    pointers = [u24(blob, start + 12 + 3 * k)
                for k in range(3 * pointer_groups)]
    period = u24(blob, start + 1)
    return {
        "start": start,
        "period_ns": period,
        "frequency_hz": 1_000_000_000 / period,
        "pointers": [pointer for pointer in pointers if pointer],
    }


def symbol_table(blob: bytes, address: int) -> list[int]:
    start = address - BASE
    return [u24(blob, start + 1 + 3 * k) for k in range(blob[start])]


def symbol(blob: bytes, address: int) -> list[int]:
    start = address - BASE
    return [u16(blob, start + 2 + 2 * k) for k in range(u16(blob, start))]


def body(blob: bytes, address: int) -> dict:
    start = address - BASE
    table = u24(blob, start)
    count = u16(blob, start + 3)
    indices = list(blob[start + 5:start + 5 + count])
    symbols = symbol_table(blob, table)
    assert all(index < len(symbols) for index in indices)
    words = [word for index in indices for word in symbol(blob, symbols[index])]
    return {"table": table, "indices": indices, "words": words}


def nec_frame(words: list[int]) -> tuple[int, int, int, int] | None:
    """Decode the first ordinary NEC frame in an expanded class-5 body."""
    header = next((k for k in range(len(words) - 1)
                   if words[k] & 0x8000
                   and 8_000 <= (words[k] & 0x7FFF) <= 10_000
                   and not words[k + 1] & 0x8000
                   and 3_500 <= words[k + 1] <= 5_500), None)
    if header is None or header + 66 > len(words):
        return None
    bits = []
    at = header + 2
    for _ in range(32):
        mark, space = words[at], words[at + 1]
        if not mark & 0x8000 or space & 0x8000:
            return None
        mark_us, space_us = mark & 0x7FFF, space & 0x7FFF
        if not 350 <= mark_us <= 800:
            return None
        if 350 <= space_us <= 900:
            bits.append(0)
        elif 1_200 <= space_us <= 2_100:
            bits.append(1)
        else:
            return None
        at += 2
    values = tuple(sum(bits[base + bit] << bit for bit in range(8))
                   for base in range(0, 32, 8))
    if values[0] ^ values[1] != 0xFF or values[2] ^ values[3] != 0xFF:
        return None
    return values


def action_addresses(blob: bytes, sections: list[int | None]) -> list[int]:
    table = sections[semantics.ACTION_SLOT]
    assert table is not None
    return [u24(blob, table + 2 + 3 * k) - BASE
            for k in range(u16(blob, table))]


def action_closure(blob: bytes, addresses: list[int], index: int,
                   seen: set[int] | None = None):
    seen = set() if seen is None else seen
    if index in seen:
        return []
    seen.add(index)
    result = []
    for opcode, operand in semantics.instructions(blob, addresses[index]):
        result.append((opcode, operand))
        if opcode == 0x7F:
            result.extend(action_closure(blob, addresses, operand, seen))
    return result


def mode_bindings(blob: bytes, sections: list[int | None], mode_index: int):
    """Return physical/default and LCD-page tags with their IR sends."""
    table = sections[semantics.MODE_SLOT]
    assert table is not None
    entry = u24(blob, table + 3 + 3 * mode_index) - BASE
    record_start = u24(blob, entry + 1) - BASE
    lists = [("physical", None, record_start)]
    for page_index in range(u16(blob, entry + 4)):
        page = u24(blob, entry + 6 + 3 * page_index) - BASE
        lists.append(("lcd", page_index, u24(blob, page) - BASE))

    addresses = action_addresses(blob, sections)
    result = []
    for source, page, start in lists:
        for tag, opcode, operand in semantics.tagged_instructions(blob, start):
            actions = (action_closure(blob, addresses, operand)
                       if opcode == 0x7F else [(opcode, operand)])
            for operation, value in actions:
                if operation == 0x7D:
                    result.append({"source": source, "page": page, "tag": tag,
                                   "group": value >> 8, "command": value & 0xFF})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_BLOB)
    parser.add_argument("--group", type=int, default=3)
    parser.add_argument("--mode", type=int, default=111)
    args = parser.parse_args()

    blob = _paths.get_blob(args.config)
    sections = semantics.section_offsets(blob)
    groups = ir_groups(blob, sections)
    if not 0 <= args.group < len(groups):
        raise SystemExit(f"IR group {args.group} does not exist")

    records = []
    table_counts = collections.Counter()
    for command, address in enumerate(groups[args.group]):
        header = record(blob, address)
        bodies = [body(blob, pointer) for pointer in header["pointers"]]
        primary_table = bodies[0]["table"] if bodies else None
        if primary_table is not None:
            table_counts[primary_table] += 1
        records.append({"command": command, **header, "bodies": bodies,
                        "primary_table": primary_table})

    dominant, dominant_count = table_counts.most_common(1)[0]
    outliers = [item for item in records if item["primary_table"] != dominant]
    bindings = mode_bindings(blob, sections, args.mode)
    by_command = collections.defaultdict(list)
    for binding in bindings:
        if binding["group"] == args.group:
            by_command[binding["command"]].append(binding)

    signals = collections.defaultdict(list)
    for item in outliers:
        frame = nec_frame(item["bodies"][0]["words"]) if item["bodies"] else None
        signals[frame].append(item)

    print("IR group sizes:", [len(group) for group in groups])
    print(f"group {args.group}: {len(records)} records; mode {args.mode}")
    print("primary symbol tables:")
    for table, count in table_counts.most_common():
        marker = " (dominant/inherited candidate)" if table == dominant else " (outlier)"
        print(f"  0x{table - BASE:06X}: {count}{marker}")
    print(f"outlier command records: {[item['command'] for item in outliers]}")
    print("unique outlier signals:")
    for frame, items in sorted(signals.items(), key=lambda pair: pair[1][0]["command"]):
        commands = [item["command"] for item in items]
        frequencies = sorted({round(item["frequency_hz"]) for item in items})
        encoded = "unknown" if frame is None else " ".join(f"{value:02X}" for value in frame)
        attached = [binding for command in commands for binding in by_command[command]]
        where = ", ".join(
            f"{binding['source']} tag=0x{binding['tag']:02X}"
            + (f" page={binding['page']}" if binding["page"] is not None else "")
            for binding in attached) or "not bound in this mode"
        print(f"  NEC {encoded}: records={commands}, carrier={frequencies} Hz, {where}")

    # The public sample's outlier family has captured timing jitter, six valid
    # NEC frames and no LCD-page use. Keep this as an explicit regression check.
    if args.group == 3 and args.mode == 111 and args.config == _paths.SAMPLE_BLOB:
        assert [item["command"] for item in outliers] == [4, 9, 19, 21, 25, 27,
                                                          29, 31, 41, 56, 58]
        assert len(signals) == 6 and None not in signals
        assert not any(binding["source"] == "lcd"
                       for command in (item["command"] for item in outliers)
                       for binding in by_command[command])
        print("PASS public X96 outlier-family regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
