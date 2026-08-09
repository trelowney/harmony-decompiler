"""Verify firmware-backed Harmony 525 semantic claims without private samples.

This is intentionally an evidence check, not a second decompiler. It reads only
the public 525 config bundled with this repository and, optionally, a local
firmware image that must never be committed.

Usage:
    python tools/verify_525_semantics.py
    python tools/verify_525_semantics.py --firmware path/to/mcu.bin
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

import _paths  # noqa: F401
import hconfig

CONFIG_BASE = 0x20000
MODE_SLOT = 6
IR_SLOT = 5
PAGE_BINDING_SLOT = 8
ACTION_SLOT = 10
STATE_SLOT = 13
SCREEN_SLOT = 11

# Operand bytes in the 525 screen language. These widths are independently
# checked below by walking every public program to its terminator.
SCREEN_FIXED = {1: 6, 2: 5, 3: 9, 4: 5, 16: 1, 17: 3, 22: 1, 23: 0}


def u16(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 2], "little")


def u24(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 3], "little")


def section_offsets(blob: bytes) -> list[int | None]:
    """Return arch-9 section offsets from the root pointer table."""
    marker = blob.index(hconfig.HEADER_MARKER)
    raw = [int.from_bytes(blob[p:p + 4], "little")
           for p in range(hconfig.PTR_TABLE_OFF, marker, 4)]
    while raw and raw[-1] == 0:
        raw.pop()
    return [address - CONFIG_BASE if address else None for address in raw]


def tagged_list_length(blob: bytes, offset: int) -> int:
    """Length of either tagged-list representation used by a mode page."""
    count = blob[offset]
    if count:
        return 1 + 4 * count
    return 2 + 5 * blob[offset + 1]


def instructions(blob: bytes, offset: int) -> list[tuple[int, int]]:
    """Read one ordinary action list as ``(opcode, operand)`` pairs."""
    count = blob[offset]
    return [(blob[offset + 3 + 3 * k], u16(blob, offset + 1 + 3 * k))
            for k in range(count)]


def tagged_instructions(blob: bytes, offset: int) -> list[tuple[int, int, int]]:
    """Read one narrow/wide page list as ``(tag, opcode, operand)`` triples."""
    wide = blob[offset] == 0
    count = blob[offset + 1] if wide else blob[offset]
    stride = 5 if wide else 4
    base = offset + (2 if wide else 1)
    result = []
    for k in range(count):
        entry = base + stride * k
        tag_at = entry + (1 if wide else 0)
        operand_at = entry + (2 if wide else 1)
        opcode_at = entry + (4 if wide else 3)
        result.append((blob[tag_at], blob[opcode_at], u16(blob, operand_at)))
    return result


def verify_slot8(blob: bytes, sections: list[int | None]) -> dict:
    start = sections[PAGE_BINDING_SLOT]
    end = sections[PAGE_BINDING_SLOT + 1]
    mode = sections[MODE_SLOT]
    assert start is not None and end is not None and mode is not None

    leading = instructions(blob, start)
    first_page_list = start + 1 + 3 * len(leading)

    mode_count = u24(blob, mode)
    entry_addresses = [u24(blob, mode + 3 + 3 * k) for k in range(mode_count)]
    page_lists = []
    page_count = 0
    for address in entry_addresses:
        entry = address - CONFIG_BASE
        pages = u16(blob, entry + 4)
        page_count += pages
        for k in range(pages):
            page = u24(blob, entry + 6 + 3 * k) - CONFIG_BASE
            page_lists.append(u24(blob, page) - CONFIG_BASE)

    assert len(page_lists) == len(set(page_lists)), "page-list addresses repeat"
    ordered = sorted(page_lists)
    assert ordered and ordered[0] == first_page_list
    cursor = first_page_list
    entries = []
    for offset in ordered:
        assert offset == cursor, f"gap or overlap before page list 0x{offset:X}"
        length = tagged_list_length(blob, offset)
        entries.extend(tag for tag, _opcode, _operand
                       in tagged_instructions(blob, offset))
        cursor += length

    assert cursor == end, f"page lists end at 0x{cursor:X}, slot 8 ends at 0x{end:X}"
    assert all(tag & 0xC0 == 0x80 for tag in entries)
    return {
        "start": start,
        "end": end,
        "length": end - start,
        "leading_instructions": len(leading),
        "leading_bytes": first_page_list - start,
        "mode_records": mode_count,
        "pages": page_count,
        "page_list_bytes": end - first_page_list,
        "tagged_entries": len(entries),
        "scan_codes": sorted({tag & 0x3F for tag in entries}),
    }


def all_action_instructions(blob: bytes, sections: list[int | None]):
    table = sections[ACTION_SLOT]
    assert table is not None
    count = u16(blob, table)
    addresses = [u24(blob, table + 2 + 3 * k) for k in range(count)]
    return [item for address in addresses
            for item in instructions(blob, address - CONFIG_BASE)]


def verify_action_closures(blob: bytes, sections: list[int | None]) -> dict:
    state = sections[STATE_SLOT]
    assert state is not None
    state_count, narrow, wide, narrow_again = (
        u16(blob, state + 2 * k) for k in range(4))
    assert narrow + wide == state_count and narrow_again == narrow

    actions = all_action_instructions(blob, sections)
    writes = [(opcode & 0x7F, operand) for opcode, operand in actions
              if opcode >= 0x80]
    assert writes and all(index < state_count for index, _ in writes)
    assert all(operand < 0x100 for index, operand in writes if index < narrow)

    quantities = [(operand >> 8, operand & 0xFF) for opcode, operand in actions
                  if opcode == 0x7C]
    ir = sections[5]
    assert ir is not None
    ir_groups = blob[ir]
    group_addresses = [u24(blob, ir + 1 + 3 * k) - CONFIG_BASE
                       for k in range(ir_groups)]
    group_sizes = [u16(blob, address + 1) for address in group_addresses]
    assert quantities and all(group < ir_groups for group, _ in quantities)

    sends = [(operand >> 8, operand & 0xFF) for opcode, operand in actions
             if opcode == 0x7D]
    assert sends and all(group < ir_groups for group, _ in sends)
    for group, size in enumerate(group_sizes):
        commands = {command for found, command in sends if found == group}
        assert commands == set(range(size))

    return {
        "action_instructions": len(actions),
        "state_count": state_count,
        "narrow": narrow,
        "wide": wide,
        "state_writes": len(writes),
        "state_write_opcodes": sorted({0x80 | index for index, _ in writes}),
        "ir_group_records": group_sizes,
        "ir_sends": len(sends),
        "ir_send_groups": dict(sorted(collections.Counter(g for g, _ in sends).items())),
        "quantity_uses": len(quantities),
        "quantity_groups": dict(sorted(collections.Counter(g for g, _ in quantities).items())),
        "quantity_values": dict(sorted(collections.Counter(v for _, v in quantities).items())),
    }


def verify_device_page_groups(blob: bytes, sections: list[int | None]) -> dict:
    """Tie each four-device menu to one base-slot-5 infrared group.

    The human-readable names are established by rendering these modes; this
    check pins the independent binary half of that result. Every page binding
    calls an action list, every reachable IR send stays in the expected group,
    and every command index is inside that group's record count.
    """
    ir = sections[IR_SLOT]
    mode = sections[MODE_SLOT]
    action = sections[ACTION_SLOT]
    assert ir is not None and mode is not None and action is not None

    group_count = blob[ir]
    group_addresses = [u24(blob, ir + 1 + 3 * k) - CONFIG_BASE
                       for k in range(group_count)]
    group_sizes = []
    for address in group_addresses:
        assert blob[address] == 0
        group_sizes.append(u16(blob, address + 1))
    assert group_sizes == [8, 67, 61, 64]

    action_count = u16(blob, action)
    action_addresses = [u24(blob, action + 2 + 3 * k) - CONFIG_BASE
                        for k in range(action_count)]

    def closure(index: int, seen: set[int] | None = None):
        assert index < action_count
        seen = set() if seen is None else seen
        if index in seen:
            return []
        seen.add(index)
        result = []
        for opcode, operand in instructions(blob, action_addresses[index]):
            result.append((opcode, operand))
            if opcode == 0x7F:
                result.extend(closure(operand, seen))
        return result

    # Renderer-confirmed menu titles: Amplifier Genius, TV Panasonic,
    # XBOX 360 and X96 Box respectively. The non-numeric labels deliberately
    # remain comments: the executable assertion is on the binary structure.
    device_modes = {
        "Amplifier Genius": (73, 0),
        "TV Panasonic": (78, 1),
        "XBOX 360": (113, 2),
        "X96 Box": (111, 3),
    }
    result = {}
    for name, (mode_index, expected_group) in device_modes.items():
        entry = u24(blob, mode + 3 + 3 * mode_index) - CONFIG_BASE
        page_count = u16(blob, entry + 4)
        sends = []
        bindings = 0
        for page_index in range(page_count):
            page = u24(blob, entry + 6 + 3 * page_index) - CONFIG_BASE
            page_list = u24(blob, page) - CONFIG_BASE
            for _tag, opcode, operand in tagged_instructions(blob, page_list):
                bindings += 1
                reachable = closure(operand) if opcode == 0x7F else [(opcode, operand)]
                sends.extend(value for operation, value in reachable if operation == 0x7D)
        assert sends and len(sends) == bindings
        assert {value >> 8 for value in sends} == {expected_group}
        commands = sorted({value & 0xFF for value in sends})
        assert all(command < group_sizes[expected_group] for command in commands)
        result[name] = {
            "mode": mode_index,
            "pages": page_count,
            "bindings": bindings,
            "ir_group": expected_group,
            "group_records": group_sizes[expected_group],
            "commands_used": len(commands),
        }
    return result


def screen_program(blob: bytes, offset: int) -> list[dict]:
    """Walk one 525 screen stream, returning raw operands for each opcode."""
    result = []
    while True:
        start = offset
        opcode = blob[offset]
        offset += 1
        if opcode == 0:
            result.append({"offset": start, "opcode": opcode, "operands": b""})
            return result
        if opcode == 20:  # unconditional transfer ends this linear stream
            operands = blob[offset:offset + 3]
            result.append({"offset": start, "opcode": opcode, "operands": operands})
            return result
        if opcode in SCREEN_FIXED:
            length = SCREEN_FIXED[opcode]
            operands = blob[offset:offset + length]
            assert len(operands) == length
            result.append({"offset": start, "opcode": opcode, "operands": operands})
            offset += length
            continue
        if opcode == 5:
            body = offset
            offset += 2  # x, y
            while blob[offset] != 0:
                offset += 2 if blob[offset] & 0x80 else 1
            offset += 1  # terminator
            result.append({"offset": start, "opcode": opcode,
                           "operands": blob[body:offset]})
            continue
        if opcode in (18, 19):
            width = 2 if opcode == 19 else 1
            body = offset
            offset += 1  # state-variable index
            for entry_length in (width + 3, 2 * width + 3):
                count = int.from_bytes(blob[offset:offset + width], "little")
                offset += width + count * entry_length
            result.append({"offset": start, "opcode": opcode,
                           "operands": blob[body:offset]})
            return result
        raise AssertionError(f"unknown screen opcode {opcode} at 0x{start:X}")


def screen_program_path(blob: bytes, offset: int) -> list[dict]:
    """Walk one screen path, following unconditional opcode-20 transfers."""
    result = []
    seen = set()
    while offset not in seen:
        seen.add(offset)
        program = screen_program(blob, offset)
        result.extend(program)
        last = program[-1]
        if last["opcode"] != 20:
            return result
        offset = u24(last["operands"], 0) - CONFIG_BASE
        assert 0 <= offset < len(blob)
    raise AssertionError(f"screen jump cycle reaches 0x{offset:X}")


def verify_screen_programs(blob: bytes, sections: list[int | None]) -> dict:
    """Close slot-11 wrappers and every mode-page program on the public 525."""
    table = sections[SCREEN_SLOT]
    mode = sections[MODE_SLOT]
    assert table is not None and mode is not None

    table_count = u16(blob, table)
    table_roots = [u24(blob, table + 2 + 3 * k) - CONFIG_BASE
                   for k in range(table_count)]
    wrappers = [screen_program_path(blob, root) for root in table_roots]
    assert all([item["opcode"] for item in program] == [17, 0]
               for program in wrappers)

    mode_count = u24(blob, mode)
    page_roots = []
    for k in range(mode_count):
        entry = u24(blob, mode + 3 + 3 * k) - CONFIG_BASE
        for page_index in range(u16(blob, entry + 4)):
            page = u24(blob, entry + 6 + 3 * page_index) - CONFIG_BASE
            page_roots.append(u24(blob, page + 3) - CONFIG_BASE)
    assert len(page_roots) == len(set(page_roots))
    programs = [screen_program_path(blob, root) for root in page_roots]
    assert all(program[-1]["opcode"] == 0 for program in programs)

    opcode_counts = collections.Counter(item["opcode"]
                                         for program in programs for item in program)
    rows = collections.Counter()
    row_blocks = 0
    for program in programs:
        starts = [index for index, item in enumerate(program) if item["opcode"] == 22]
        commits = [index for index, item in enumerate(program) if item["opcode"] == 23]
        assert len(starts) == 8 and len(commits) == 8
        for index, item in enumerate(program):
            if item["opcode"] != 22:
                continue
            row = item["operands"][0]
            draw = program[index + 1]
            assert draw["opcode"] == 3
            operands = draw["operands"]
            assert operands[:6] == bytes((0, 8 * row, 0, 8 * row, 96, 8))
            next_start = next((at for at in starts if at > index), len(program))
            block_commits = [at for at in commits if index < at < next_start]
            assert len(block_commits) == 1, "each selected row must be transferred once"
            rows[row] += 1
            row_blocks += 1
    assert rows == collections.Counter({row: len(programs) for row in range(8)})
    assert row_blocks == 8 * len(programs)

    # Base slot 7 is a u16-counted table of font-set pointers on this 525. Each
    # string code must land on a non-null glyph in the font selected by opcode
    # 16. This proves the strings are glyph indices rather than guessed text.
    font_table = sections[7]
    assert font_table is not None
    fonts = []
    for index in range(u16(blob, font_table)):
        start = u24(blob, font_table + 2 + 3 * index) - CONFIG_BASE
        height, second, third = blob[start:start + 3]
        first = 1 if third == 0 else second
        count = second if third == 0 else third
        glyphs = [u24(blob, start + 3 + 3 * k) for k in range(count)]
        fonts.append({"height": height, "first": first, "glyphs": glyphs})

    string_count = 0
    glyph_code_count = 0
    for program in programs:
        font_index = 0
        for item in program:
            opcode, operands = item["opcode"], item["operands"]
            if opcode == 16:
                font_index = operands[0]
                assert font_index < len(fonts)
                continue
            codes = None
            if opcode == 5:
                assert operands[-1] == 0
                codes = operands[2:-1]
            elif opcode == 4:
                address = u24(operands, 2) - CONFIG_BASE
                end = blob.index(0, address)
                codes = blob[address:end]
            if codes is None:
                continue
            string_count += 1
            font = fonts[font_index]
            for code in codes:
                glyph = code - font["first"]
                assert 0 <= glyph < len(font["glyphs"])
                assert font["glyphs"][glyph] != 0
                glyph_code_count += 1

    return {
        "slot11_wrappers": len(wrappers),
        "page_programs": len(programs),
        "page_program_opcodes": dict(sorted(opcode_counts.items())),
        "row_blocks": row_blocks,
        "rows": dict(sorted(rows.items())),
        "font_sets": len(fonts),
        "strings": string_count,
        "resolved_glyph_codes": glyph_code_count,
    }


def verify_tone_firmware(path: Path) -> dict:
    """Pin the 525 0x75 handler and GPIO toggle without redistributing firmware."""
    import pic18dis

    data = path.read_bytes()
    assert len(data) == 32768, "expected the 32 KiB 525 MCU image"
    listing = {address: text for address, _words, text, _target
               in pic18dis.disassemble(data)}
    expected = {
        0x01DC4: "MOVFF 0x3D7, 0x1FA",
        0x01DC8: "MOVFF 0x3D8, 0x1F9",
        0x01DCC: "CALL 0x056D8",
        0x05798: "BTG LATA, 2",
    }
    for address, text in expected.items():
        assert listing.get(address) == text, (
            f"firmware mismatch at 0x{address:05X}: {listing.get(address)!r}")
    return {"size": len(data), "handler": "0x01DC4", "toggle": "LATA.2"}


def verify_screen_firmware(path: Path) -> dict:
    """Pin the 525 row-select and row-transfer paths in the screen dispatcher."""
    import pic18dis

    data = path.read_bytes()
    listing = {address: text for address, _words, text, _target
               in pic18dis.disassemble(data)}
    expected = {
        # Opcode 22 reads one row byte and calls the row-window calculation.
        0x046D6: "CALL 0x06576",
        0x046DE: "MOVFF 0xFE8, 0x0D9",
        0x046E2: "CALL 0x038EC",
        # Opcode 23 selects the display, transfers a 96-pixel row, then releases it.
        0x046E8: "BSF LATE, 2",
        0x046EA: "BCF LATA, 5",
        0x046EE: "MOVLW 0x60",
        0x04714: "CALL 0x03898",
        0x04718: "BSF LATA, 5",
        # Row n becomes inclusive pixel rows 8*n through 8*n+7.
        0x038F4: "MULWF 0xD9",
        0x038FA: "MOVLW 0x07",
    }
    for address, text in expected.items():
        assert listing.get(address) == text, (
            f"screen firmware mismatch at 0x{address:05X}: {listing.get(address)!r}")
    return {"dispatcher": "0x04650", "row_select": "0x16", "row_transfer": "0x17",
            "width": 96, "row_height": 8}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_BLOB)
    parser.add_argument("--firmware", type=Path)
    args = parser.parse_args()

    blob = _paths.get_blob(args.config)
    if blob[:4] != hconfig.MAGIC:
        raise SystemExit("this verifier currently pins the public arch-9 525 evidence")
    sections = section_offsets(blob)
    slot8 = verify_slot8(blob, sections)
    action = verify_action_closures(blob, sections)
    devices = verify_device_page_groups(blob, sections)
    screen = verify_screen_programs(blob, sections)

    print("PASS slot 8 closes exactly")
    print("  " + ", ".join(f"{k}={v}" for k, v in slot8.items()))
    print("PASS state writes and 0x7C operands close against their tables")
    print("  " + ", ".join(f"{k}={v}" for k, v in action.items()))
    print("PASS rendered device modes close against their IR groups")
    for name, details in devices.items():
        print(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in details.items()))
    print("PASS all slot-11 and mode-page screen programs decode")
    print("  " + ", ".join(f"{k}={v}" for k, v in screen.items()))
    if args.firmware:
        firmware = verify_tone_firmware(args.firmware)
        print("PASS opcode 0x75 reaches a counted LATA.2 toggle loop")
        print("  " + ", ".join(f"{k}={v}" for k, v in firmware.items()))
        screen_firmware = verify_screen_firmware(args.firmware)
        print("PASS screen opcodes 22/23 select and transfer one 96x8 row")
        print("  " + ", ".join(f"{k}={v}" for k, v in screen_firmware.items()))
    else:
        print("SKIP firmware check (pass --firmware; never commit the image)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
