"""Build an offline-only Samsung T200HD device candidate for the owner H525.

The command payloads come from a caller-supplied public IRDB CSV for Samsung
device/subdevice 7/7 and are independently checked against the LIRC capture
for remote BN59-00678A.  Frame timing comes from that capture; once/held/tail
slot roles and the 50/500 ms wrappers come from this owner's configuration.

The candidate adds IR group 4, mode 114, 39 self-contained class-5 records,
28 known physical-key bindings and three four-cell LCD pages.  It never opens
USB hardware, refuses source overwrite and is not a hardware-safe writer.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import re

import _paths
import analyze_525_ir as ir
import hconfig
import render_525_screens as renderer
import verify_525_semantics as semantics
from class5_ir_encoder import (
    Pulse,
    decode_class5_record,
    encode_class5_record,
    words_from_pulses,
)
from clone_525_device import (
    fresh_region,
    mode_fields,
    opaque,
    region_at,
    render_map,
    sha256,
    symbol_for_offset,
    tagged_entries,
    class5_source,
)
from h525_lcd_author import alphabet_map, encode_text


TARGET_GROUP = 4
TARGET_MODE = 114
MENU_MODE = 45
PANASONIC_MODE = 78
EXPECTED_GROUP_SIZES = [8, 67, 61, 64]
EXPECTED_MODE_COUNT = 114
EXPECTED_ACTION_COUNT = 487
MENU_TAG = 0x9E

PERIOD_NS = 26_315
# Normalized from four owner-labelled physical RM-D613 captures on 2026-08-21.
# Unlike NEC1, Samsung32/NECx2 repeats the whole frame. Power, digit 1, held
# Volume+ and Pre-CH all measured 38 kHz, 4.472..4.474 ms headers, 560 us marks
# and 108.484..108.524 ms start-to-start cadence. The terminal mark/gap split
# below preserves the measured 108.504 ms centre while using a full 560 us unit.
FULL_HEADER = ((True, 4_474), (False, 4_474))
BIT_MARK_US = 560
ZERO_SPACE_US = 560
ONE_SPACE_US = 1_678
TRAIL_MARK_US = 560
FRAME_GAP_US = 47_504

DEVICES_CODES = bytes((20, 7, 49, 10, 25, 7, 26))
AMPLIFIER_CODES = bytes((45, 9, 21, 32, 10, 31, 10, 7, 8))
GENIUS_CODES = bytes((36, 7, 11, 10, 24, 26))
DONOR_TITLE_CODES = AMPLIFIER_CODES + bytes((13,)) + GENIUS_CODES
DONOR_LABELS = {
    bytes((43, 35, 8)): "upper_left_first",       # Cmd
    bytes((6, 29, 22, 22, 32, 7)): "upper_left_second",  # Toggle
    bytes((39, 11, 21, 24, 12)): "upper_right_first",    # Input
    bytes((36, 5, 9, 7)): "upper_right_second",   # Game
    bytes((39, 11, 21, 24, 12, 6, 49)): "lower_left",    # InputTV
    bytes((39, 11, 21, 24, 12, 20, 50, 20)): "lower_right",  # InputDVD
}

# Panasonic group-1 commands are the owner-config oracle for the printed 525
# button roles.  Values are IRDB function numbers for the Samsung command that
# belongs on the same physical key.  Guide (Panasonic command 9) is deliberately
# left unbound because the remote-specific BN59-00678A source has no Guide row.
PANASONIC_TO_SAMSUNG_FUNCTION = {
    14: 6, 55: 5, 34: 8, 58: 10, 47: 9, 49: 12, 28: 14, 12: 13,
    35: 31, 7: 17, 50: 108, 45: 26, 66: 45, 42: 22, 4: 21,
    15: 20, 22: 15, 48: 16, 61: 4, 52: 98, 32: 96, 44: 97,
    2: 11, 57: 7, 59: 104, 18: 101, 41: 18,
}
# Direct owner-measured 525 physical oracle: Prev is event A4 / scan 36.  The
# owner's RM-D613 then physically confirmed its Pre-CH payload as function 19.
DIRECT_EVENT_TO_SAMSUNG_FUNCTION = {0xA4: 19}

REPEATABLE_FUNCTIONS = {7, 11, 16, 18, 96, 97, 98, 101, 104}
LCD_PAGES = (
    (("Power", 2), ("Source", 1), ("Tools", 75), ("P.Size", 62)),
    (("TV", 27), ("Return", 88), ("Fav.Ch", 68), ("CC", 37)),
    (("SRS", 110), ("MTS", 0), ("Dashes", 35), None),
)
LCD_TAGS = (0xA7, 0xA6, 0x9F, 0x9E)


def reverse_byte(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def load_irdb(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for raw in csv.DictReader(stream):
            row = {
                "name": raw["functionname"],
                "protocol": raw["protocol"],
                "device": int(raw["device"]),
                "subdevice": int(raw["subdevice"]),
                "function": int(raw["function"]),
            }
            rows.append(row)
    if len(rows) != 39:
        raise SystemExit(f"IRDB profile has {len(rows)} rows, expected 39")
    if len({row["function"] for row in rows}) != len(rows):
        raise SystemExit("IRDB profile repeats a function number")
    if any((row["protocol"], row["device"], row["subdevice"]) != ("NECx2", 7, 7)
           for row in rows):
        raise SystemExit("IRDB profile is not uniformly NECx2 device 7/subdevice 7")
    return sorted(rows, key=lambda row: row["function"])


def parse_lirc(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"\bpre_data\s+0xE0E0\b", text):
        raise SystemExit("LIRC source does not declare Samsung pre_data 0xE0E0")
    result = {}
    inside = False
    for line in text.splitlines():
        if "begin codes" in line:
            inside = True
            continue
        if inside and "end codes" in line:
            break
        if inside and (match := re.match(r"\s*(\S+)\s+0x([0-9A-Fa-f]{4})\b", line)):
            result[match.group(1)] = int(match.group(2), 16)
    if len(result) != 39:
        raise SystemExit(f"LIRC source has {len(result)} codes, expected 39")
    return result


def verify_public_sources(rows: list[dict], lirc: dict[str, int]) -> None:
    if {row["name"] for row in rows} != set(lirc):
        raise SystemExit("IRDB and LIRC command names do not form the same 39-row set")
    for row in rows:
        function = row["function"]
        expected = (reverse_byte(function) << 8) | reverse_byte(function ^ 0xFF)
        if lirc[row["name"]] != expected:
            raise SystemExit(
                f"public sources disagree for {row['name']}: "
                f"LIRC=0x{lirc[row['name']]:04X}, function={function}"
            )


def pulses(intervals) -> list[Pulse]:
    return [Pulse(mark, duration) for mark, duration in intervals]


def samsung_words(function: int, *, repeatable: bool) -> tuple[tuple[int, ...], ...]:
    logical_bytes = (7, 7, function, function ^ 0xFF)
    full = list(FULL_HEADER)
    for value in logical_bytes:
        for bit in range(8):
            full.append((True, BIT_MARK_US))
            full.append((False, ONE_SPACE_US if value & (1 << bit) else ZERO_SPACE_US))
    full.append((True, TRAIL_MARK_US))
    # NECx2/Samsung32 sends the complete command at least twice.  Its held form
    # is another complete frame, not NEC1's special three-pulse repeat burst.
    held_intervals = [*full, (False, FRAME_GAP_US)]
    once_intervals = [
        (False, 50_000 if repeatable else 500_000),
        *full,
        (False, FRAME_GAP_US),
        *held_intervals,
    ]
    once = words_from_pulses(pulses(once_intervals))
    held = words_from_pulses(pulses(held_intervals)) if repeatable else None
    if sum(word & 0x7FFF for word in words_from_pulses(pulses(full))) != 61_000:
        raise AssertionError("Samsung32 frame left the measured RM-D613 timing envelope")
    return once, held, None


def decode_samsung_frames(words) -> list[tuple[int, int, int, int]]:
    """Independently find complete Samsung32 frames in duration words."""
    intervals: list[list[int | bool]] = []
    for word in words:
        item = [bool(word & 0x8000), word & 0x7FFF]
        if intervals and intervals[-1][0] == item[0]:
            intervals[-1][1] += item[1]
        else:
            intervals.append(item)
    frames = []
    at = 0
    while at + 66 < len(intervals):
        if not (intervals[at][0] and 3_500 <= intervals[at][1] <= 5_500
                and not intervals[at + 1][0]
                and 3_500 <= intervals[at + 1][1] <= 5_500):
            at += 1
            continue
        bits = []
        cursor = at + 2
        for _ in range(32):
            mark, space = intervals[cursor], intervals[cursor + 1]
            if not mark[0] or space[0] or not 400 <= mark[1] <= 800:
                break
            if 350 <= space[1] <= 850:
                bits.append(0)
            elif 1_200 <= space[1] <= 2_100:
                bits.append(1)
            else:
                break
            cursor += 2
        if (len(bits) == 32 and cursor < len(intervals) and intervals[cursor][0]
                and 400 <= intervals[cursor][1] <= 800):
            frames.append(tuple(
                sum(bits[base + bit] << bit for bit in range(8))
                for base in range(0, 32, 8)
            ))
            at = cursor + 1
        else:
            at += 1
    return frames


def screen_string(blob: bytes, opcode: int, operands: bytes) -> bytes | None:
    if opcode == 4:
        at = ir.u24(operands, 2) - ir.BASE
        return blob[at:blob.index(0, at)]
    if opcode == 5:
        return operands[2:-1]
    return None


def font_width(font: renderer.Font, codes: bytes) -> int:
    return sum(len(font.glyph(code)[0]) for code in codes)


def build_device_page_program(
    blob: bytes,
    doc: dict,
    root: int,
    page_index: int,
    labels,
    alphabet: dict[int, str],
    font0: renderer.Font,
) -> list[dict]:
    """Clone the donor four-cell page while replacing every visible string."""
    encoded = [None if item is None else encode_text(item[0], alphabet) for item in labels]
    for item in encoded:
        if item is not None and font_width(font0, item) > 47:
            raise SystemExit("LCD label exceeds its 48-pixel half-screen cell")
    title = encode_text("Samsung T200HD", alphabet)
    title_x = (96 - font_width(font0, title)) // 2
    replacements = {
        "upper_left_first": (encoded[0], 0, 16),
        "upper_right_first": (encoded[1], None, 16),
        "lower_left": (encoded[2], 0, 35),
        "lower_right": (encoded[3], None, 35),
    }

    regions = []
    sequence = 0
    for instruction in semantics.screen_program_path(blob, root):
        opcode = instruction["opcode"]
        operands = instruction["operands"]
        region_id = f"samsung_page_{page_index}_screen_{sequence:02d}"
        sequence += 1
        if opcode == 3:
            target = ir.u24(operands, 6) - ir.BASE
            regions.append(fresh_region(
                "screen_picture", region_id,
                coordinates=list(operands[:6]),
                targets=[symbol_for_offset(doc, target)],
            ))
            continue
        if opcode == 0x10 and operands == b"\x03":
            regions.append(opaque(region_id, b"\x10\x00"))
            continue
        codes = screen_string(blob, opcode, operands)
        if codes == bytes((55,)) and operands[1] == 56:  # current page number
            current = encode_text(str(page_index + 1), alphabet)
            regions.append(opaque(
                region_id, bytes((5, operands[0], operands[1])) + current + b"\x00"))
            continue
        if codes == bytes((30,)) and operands[1] == 56:  # donor total was two
            total = encode_text(str(len(LCD_PAGES)), alphabet)
            regions.append(opaque(
                region_id, bytes((5, operands[0], operands[1])) + total + b"\x00"))
            continue
        if codes == DONOR_TITLE_CODES:
            regions.append(opaque(
                region_id,
                bytes((5, title_x, operands[1])) + title + b"\x00",
            ))
            continue
        role = DONOR_LABELS.get(codes) if codes is not None else None
        if role in ("upper_left_second", "upper_right_second"):
            continue
        if role in replacements:
            value, x, y = replacements[role]
            if value is None:
                continue
            if x is None:
                x = 96 - font_width(font0, value)
            regions.append(opaque(region_id, bytes((5, x, y)) + value + b"\x00"))
            continue
        if opcode in (4, 5):
            if opcode == 4:
                target = ir.u24(operands, 2) - ir.BASE
                regions.append(fresh_region(
                    "screen_reference", region_id, opcode="0x04",
                    x=operands[0], y=operands[1],
                    targets=[symbol_for_offset(doc, target)],
                ))
            else:
                regions.append(opaque(region_id, bytes((opcode,)) + operands))
            continue
        regions.append(opaque(region_id, bytes((opcode,)) + operands))
    return regions


def build_menu_program(blob: bytes, doc: dict, root: int, alphabet: dict[int, str],
                       font0: renderer.Font) -> list[dict]:
    samsung = encode_text("Samsung", alphabet)
    model = encode_text("T200HD", alphabet)
    model_x = 96 - font_width(font0, model)
    regions = []
    sequence = 0
    for instruction in semantics.screen_program_path(blob, root):
        opcode = instruction["opcode"]
        operands = instruction["operands"]
        region_id = f"samsung_menu_screen_{sequence:02d}"
        sequence += 1
        if opcode == 3:
            target = ir.u24(operands, 6) - ir.BASE
            regions.append(fresh_region(
                "screen_picture", region_id,
                coordinates=list(operands[:6]),
                targets=[symbol_for_offset(doc, target)],
            ))
            continue
        codes = screen_string(blob, opcode, operands)
        if codes == DEVICES_CODES:
            regions.append(opaque(region_id, bytes((5, operands[0], operands[1]))
                                  + DEVICES_CODES + b"\x00"))
        elif codes == AMPLIFIER_CODES:
            regions.append(opaque(region_id, bytes((5, 50, operands[1])) + samsung + b"\x00"))
        elif codes == GENIUS_CODES:
            regions.append(opaque(region_id, bytes((5, model_x, operands[1])) + model + b"\x00"))
        elif opcode not in (4, 5):
            regions.append(opaque(region_id, bytes((opcode,)) + operands))
    return regions


def clone(source: Path, output: Path, irdb_csv: Path, lirc_config: Path) -> dict:
    if source.resolve() == output.resolve():
        raise SystemExit("refusing to overwrite source config")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")

    rows = load_irdb(irdb_csv)
    lirc = parse_lirc(lirc_config)
    verify_public_sources(rows, lirc)
    by_function = {row["function"]: index for index, row in enumerate(rows)}

    raw = source.read_bytes()
    before_blob = hconfig.split_container(raw)[2]
    before_sections = semantics.section_offsets(before_blob)
    before_groups = ir.ir_groups(before_blob, before_sections)
    if [len(group) for group in before_groups] != EXPECTED_GROUP_SIZES:
        raise SystemExit("source IR group population changed")
    before_ir_semantics = [
        [class5_source(before_blob, address) for address in group]
        for group in before_groups
    ]
    if ir.u24(before_blob, before_sections[semantics.MODE_SLOT]) != EXPECTED_MODE_COUNT:
        raise SystemExit("source mode count changed")
    if ir.u16(before_blob, before_sections[semantics.ACTION_SLOT]) != EXPECTED_ACTION_COUNT:
        raise SystemExit("source action count changed")

    doc = hconfig.symbolise(hconfig.decompile(raw, source.name))
    regions = doc["blob"]["regions"]
    fonts = renderer.fonts(before_blob, before_sections)
    alphabet = alphabet_map()
    donor = mode_fields(before_blob, before_sections, 73)
    menu = mode_fields(before_blob, before_sections, MENU_MODE)
    panasonic = mode_fields(before_blob, before_sections, PANASONIC_MODE)
    if len(donor["pages"]) != 2 or len(menu["pages"]) != 1:
        raise SystemExit("source donor/menu page shape changed")

    # One fixed-size seed per row, followed by a second pass at final addresses.
    record_specs = []
    record_regions = []
    for index, row in enumerate(rows):
        repeatable = row["function"] in REPEATABLE_FUNCTIONS
        streams = samsung_words(row["function"], repeatable=repeatable)
        seed = encode_class5_record(
            period_ns=PERIOD_NS,
            pointer_streams=streams,
            base_address=0x60000,
        )
        region = opaque(f"samsung_class5_{index:02d}", seed.blob)
        record_specs.append((row, streams, seed, region))
        record_regions.append(region)

    new_group = fresh_region(
        "ir_group", "samsung_ir_group",
        targets=[
            {"to": region["id"], "delta": seed.record_address - seed.base_address}
            for _row, _streams, seed, region in record_specs
        ],
    )

    # Every command has one action; QueueDelay 1 is the owner's repeatable
    # pulse-distance convention and 5 is its one-shot convention.
    action_regions = []
    for index, row in enumerate(rows):
        action_regions.append(fresh_region(
            "action_list", f"samsung_action_{EXPECTED_ACTION_COUNT + index}",
            instructions=[
                {"operand": TARGET_GROUP << 8 | index, "opcode": "0x7D"},
                {"operand": TARGET_GROUP << 8 | (1 if row["function"] in REPEATABLE_FUNCTIONS else 5),
                 "opcode": "0x7C"},
            ],
        ))
    selection_index = EXPECTED_ACTION_COUNT + len(action_regions)
    selection = fresh_region(
        "action_list", f"samsung_action_{selection_index}",
        instructions=[{"operand": TARGET_MODE, "opcode": "0x7E"},
                      {"operand": 1, "opcode": "0x92"}],
    )

    pan_entries = tagged_entries(before_blob, panasonic["start"])[1]
    pan_by_tag = {
        item["tag"]: item["command"]
        for item in ir.mode_bindings(before_blob, before_sections, PANASONIC_MODE)
        if item["source"] == "physical" and item["group"] == 1
    }
    physical_entries = copy.deepcopy(pan_entries)
    for entry in physical_entries:
        pan_command = pan_by_tag.get(entry["tag"])
        samsung_function = DIRECT_EVENT_TO_SAMSUNG_FUNCTION.get(
            entry["tag"], PANASONIC_TO_SAMSUNG_FUNCTION.get(pan_command))
        if samsung_function is None:
            entry["operand"] = 0
            entry["opcode"] = 0
        else:
            entry["operand"] = EXPECTED_ACTION_COUNT + by_function[samsung_function]
            entry["opcode"] = 0x7F
    physical = fresh_region(
        "tagged_list", "samsung_mode_physical", wide=False,
        entries=physical_entries,
    )

    page_lists = []
    page_programs = []
    page_records = []
    for page_index, labels in enumerate(LCD_PAGES):
        entries = []
        for tag, item in zip(LCD_TAGS, labels):
            if item is not None:
                entries.append({
                    "tag": tag,
                    "operand": EXPECTED_ACTION_COUNT + by_function[item[1]],
                    "opcode": 0x7F,
                })
        page_list = fresh_region(
            "tagged_list", f"samsung_page_list_{page_index}", section=8,
            wide=False, entries=entries,
        )
        program = build_device_page_program(
            before_blob, doc, donor["pages"][0]["program"], page_index,
            labels, alphabet, fonts[0],
        )
        page = fresh_region(
            "mode_page", f"samsung_mode_page_{page_index}",
            targets=[{"to": page_list["id"]}, {"to": program[0]["id"]}],
        )
        page_lists.append(page_list)
        page_programs.extend(program)
        page_records.append(page)

    mode_entry = fresh_region(
        "record_header", "samsung_mode_entry",
        back_reference={"to": physical["id"]},
        targets=[{"to": page["id"]} for page in page_records],
    )

    menu_list = fresh_region(
        "tagged_list", "samsung_menu_page_list", section=8, wide=False,
        entries=[{"tag": MENU_TAG, "operand": selection_index, "opcode": 0x7F}],
    )
    menu_program = build_menu_program(
        before_blob, doc, menu["pages"][0]["program"], alphabet, fonts[0])
    menu_page = fresh_region(
        "mode_page", "samsung_menu_page",
        targets=[{"to": menu_list["id"]}, {"to": menu_program[0]["id"]}],
    )

    ir_table = before_sections[semantics.IR_SLOT]
    mode_table = before_sections[semantics.MODE_SLOT]
    action_table = before_sections[semantics.ACTION_SLOT]
    assert ir_table is not None and mode_table is not None and action_table is not None
    region_at(doc, ir_table, "pointer_table")["targets"].append({"to": new_group["id"]})
    region_at(doc, mode_table, "pointer_table")["targets"].append({"to": mode_entry["id"]})
    region_at(doc, action_table, "pointer_table")["targets"].extend(
        {"to": action["id"]} for action in [*action_regions, selection])
    region_at(doc, menu["entry"], "record_header")["targets"].append({"to": menu_page["id"]})

    section9 = next(i for i, region in enumerate(regions) if region.get("section") == 9)
    regions[section9:section9] = [*page_lists, menu_list]
    section0 = next(i for i, region in enumerate(regions) if region.get("section") == 0)
    low = [
        *record_regions, *action_regions, selection, new_group, physical,
        *page_programs, *page_records, mode_entry, *menu_program, menu_page,
    ]
    regions[section0:section0] = low

    resolve, _ = hconfig._resolver(regions)
    placed = []
    for row, streams, seed, region in record_specs:
        base = ir.BASE + resolve({"to": region["id"]})
        record = encode_class5_record(
            period_ns=PERIOD_NS, pointer_streams=streams, base_address=base)
        if len(record.blob) != len(seed.blob):
            raise SystemExit("class-5 relocation changed record length")
        decoded = decode_class5_record(
            record.blob, base_address=base, record_address=record.record_address)
        if decoded.pointer_streams != tuple(
                None if stream is None else tuple(stream) for stream in streams):
            raise SystemExit("local class-5 decoder rejected a relocated Samsung record")
        region["data"] = hconfig._hex_lines(record.blob)
        placed.append(record)

    candidate = hconfig.compile_config(doc)
    after_blob = hconfig.split_container(candidate)[2]
    after_sections = semantics.section_offsets(after_blob)
    after_groups = ir.ir_groups(after_blob, after_sections)
    if [len(group) for group in after_groups] != [*EXPECTED_GROUP_SIZES, 39]:
        raise SystemExit("Samsung IR group did not close at 39 records")
    if [[class5_source(after_blob, address) for address in group]
            for group in after_groups[:4]] != before_ir_semantics:
        raise SystemExit("an original IR record changed")
    for index, record in enumerate(placed):
        if after_groups[TARGET_GROUP][index] != record.record_address:
            raise SystemExit("Samsung group target does not reach its placed record")
        period, streams = class5_source(after_blob, record.record_address)
        if period != PERIOD_NS or streams != record_specs[index][1]:
            raise SystemExit("placed Samsung record expanded incorrectly")
        row = record_specs[index][0]
        expected_frame = (7, 7, row["function"], row["function"] ^ 0xFF)
        once_frames = decode_samsung_frames(streams[0])
        held_frames = [] if streams[1] is None else decode_samsung_frames(streams[1])
        if once_frames != [expected_frame, expected_frame]:
            raise SystemExit(f"Samsung command {index} does not decode as two NECx2 frames")
        expected_held = [expected_frame] if row["function"] in REPEATABLE_FUNCTIONS else []
        if held_frames != expected_held or streams[2] is not None:
            raise SystemExit(f"Samsung command {index} has the wrong held/tail shape")

    bindings = ir.mode_bindings(after_blob, after_sections, TARGET_MODE)
    physical_bindings = [item for item in bindings if item["source"] == "physical"]
    lcd_bindings = [item for item in bindings if item["source"] == "lcd"]
    if len(physical_bindings) != 28 or len(lcd_bindings) != 11:
        raise SystemExit("Samsung physical/LCD binding population changed")
    if {item["group"] for item in bindings} != {TARGET_GROUP}:
        raise SystemExit("Samsung binding escapes target IR group")

    after_menu = mode_fields(after_blob, after_sections, MENU_MODE)
    if len(after_menu["pages"]) != 2:
        raise SystemExit("Devices menu did not gain exactly one page")
    after_actions = ir.action_addresses(after_blob, after_sections)
    if semantics.instructions(after_blob, after_actions[selection_index]) != [
            (0x7E, TARGET_MODE), (0x92, 1)]:
        raise SystemExit("Samsung menu selection action changed")

    before_render = render_map(before_blob)
    after_render = render_map(after_blob)
    if len(before_render) != 135 or len(after_render) != 139:
        raise SystemExit("unexpected screen population")
    changed_existing = [key for key, value in before_render.items()
                        if after_render.get(key, (None,))[0] != value[0]]
    if changed_existing:
        raise SystemExit(f"existing screen pixels changed: {changed_existing}")

    slot8 = semantics.verify_slot8(after_blob, after_sections)
    actions_check = semantics.verify_action_closures(after_blob, after_sections)
    screens_check = semantics.verify_screen_programs(after_blob, after_sections)
    rebuilt = hconfig.compile_config(hconfig.decompile(candidate, output.name))
    if rebuilt != candidate:
        raise SystemExit(f"round trip differs at {hconfig.first_difference(candidate, rebuilt)}")
    if int.from_bytes(after_blob[-6:-4], "little") != hconfig.trailer_checksum(after_blob):
        raise SystemExit("firmware trailer checksum is invalid")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(candidate)
    source_csv = irdb_csv.read_bytes()
    source_lirc = lirc_config.read_bytes()
    return {
        "schema": 1,
        "scope": "offline Samsung T200HD / RM-D613 H525 candidate; never hardware-tested",
        "status": "engineering candidate, not a reconstruction of Logitech's compiler",
        "source_config": str(source.resolve()),
        "output": str(output.resolve()),
        "source_sha256": sha256(raw),
        "output_sha256": sha256(candidate),
        "public_sources": {
            "irdb_csv": str(irdb_csv.resolve()),
            "irdb_csv_sha256": hashlib.sha256(source_csv).hexdigest(),
            "lirc_config": str(lirc_config.resolve()),
            "lirc_config_sha256": hashlib.sha256(source_lirc).hexdigest(),
            "agreement": "39/39 names and function payloads; NECx2 D=7 S=7",
            "remote_context": "RM-D613 is sold as a Samsung replacement including BN59-00624A; T200HD manuals name BN59-00678A in another region",
        },
        "candidate": {
            "display_name": "Samsung T200HD",
            "ir_group": TARGET_GROUP,
            "mode": TARGET_MODE,
            "records": len(rows),
            "physical_bindings": len(physical_bindings),
            "lcd_bindings": len(lcd_bindings),
            "lcd_pages": len(LCD_PAGES),
            "commands": [
                {**row, "index": index,
                 "repeatable": row["function"] in REPEATABLE_FUNCTIONS,
                 "shape": "BB0" if row["function"] in REPEATABLE_FUNCTIONS else "B00",
                 "record_address": placed[index].record_address,
                 "blob_sha256": hashlib.sha256(placed[index].blob).hexdigest()}
                for index, row in enumerate(rows)
            ],
        },
        "checks": {
            "irdb_lirc_39_of_39_exact": True,
            "all_200_original_ir_records_exact": True,
            "all_39_new_records_local_decode_exact": True,
            "all_39_new_records_protocol_decode_exact": True,
            "physical_role_oracle_bindings": 28,
            "lcd_soft_key_bindings": 11,
            "existing_135_screens_pixel_identical": True,
            "firmware_trailer_checksum": True,
            "semantic_round_trip_byte_identical": True,
        },
        "counts": {
            "blob_before": len(before_blob), "blob_after": len(after_blob),
            "blob_growth": len(after_blob) - len(before_blob),
            "ir_group_sizes": [len(group) for group in after_groups],
            "modes": ir.u24(after_blob, after_sections[semantics.MODE_SLOT]),
            "actions": ir.u16(after_blob, after_sections[semantics.ACTION_SLOT]),
            "screens": len(after_render),
        },
        "slot8": slot8,
        "actions": actions_check,
        "screens": screens_check,
        "known_limitations": [
            "never written to or accepted by Harmony hardware",
            "only Power, digit 1, Volume+ and Pre-CH were physically captured from this exact RM-D613 unit",
            "repeatability policy is conservative engineering choice, not Logitech compiler output",
            "Power is a device LCD command; the 525 physical Off key remains activity-owned",
            "architecture 9 / Harmony 525 only",
        ],
        "credit": {
            "class5_layout_and_once_held_tail_roles": "Danny Bloemendaal / harmony-explorations",
            "public_command_database": "irdb by Simon Peter and contributors",
            "independent_remote_capture": "SuperSmashOgre / LIRC remotes",
            "owner_config_and_rm_d613_identity": "Tomas / trelowney",
            "candidate_construction_and_verification": "trelowney Harmony project / Codex",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_EZHEX)
    parser.add_argument("--irdb-csv", required=True, type=Path)
    parser.add_argument("--lirc-config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path)
    args = parser.parse_args()
    if args.proof.exists():
        raise SystemExit(f"refusing to overwrite existing proof: {args.proof}")
    proof = clone(args.config, args.out, args.irdb_csv, args.lirc_config)
    args.proof.parent.mkdir(parents=True, exist_ok=True)
    args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("PASS: built offline Samsung T200HD / RM-D613 candidate")
    print(f"  {proof['counts']['ir_group_sizes']}, {proof['counts']['modes']} modes, "
          f"{proof['counts']['actions']} actions, {proof['counts']['screens']} screens")
    print(f"  39 records; 28 physical and 11 LCD bindings; "
          f"grew by {proof['counts']['blob_growth']} bytes")
    print(args.out.resolve())
    print(args.proof.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
