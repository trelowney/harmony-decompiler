"""Build one offline fifth-device proof by cloning the smallest 525 device.

The donor is Amplifier Genius (IR group 0, mode 73).  The generated device is
IR group 4 and mode 114.  Existing IR records and screen programs are shared;
new action lists change only the device byte from 0 to 4.  The Devices menu
gains a second page with one lower-right Amplifier Genius entry.  An optional
bounded proof replaces clone command 0 with a newly packed copy of known X96
record 9, while the other seven commands remain shared with the donor.

This is a structural experiment, not a remote-safe configuration.  It never
opens hardware and refuses to overwrite either source or output.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import _paths
import analyze_525_ir as ir
import hconfig
import render_525_screens as renderer
import verify_525_semantics as semantics
from class5_ir_encoder import decode_class5_record, encode_class5_record


DONOR_GROUP = 0
DONOR_MODE = 73
MENU_MODE = 45
EXPECTED_GROUP_SIZES = [8, 67, 61, 64]
EXPECTED_MODE_COUNT = 114
EXPECTED_ACTION_COUNT = 487
TARGET_GROUP = 4
TARGET_MODE = 114
MENU_TAG = 0x9E
X96_GROUP = 3
PLACED_X96_COMMAND = 9
PLACED_X96_NEC = (0x01, 0xFE, 0x4E, 0xB1)
CLASS5_LAYOUT_BASE = 0x60000

DEVICES_CODES = bytes((20, 7, 49, 10, 25, 7, 26))
AMPLIFIER_CODES = bytes((45, 9, 21, 32, 10, 31, 10, 7, 8))
GENIUS_CODES = bytes((36, 7, 11, 10, 24, 26))
MENU_KEEP = {DEVICES_CODES, AMPLIFIER_CODES, GENIUS_CODES}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fresh_region(kind: str, region_id: str, **fields) -> dict:
    return {"kind": kind, "id": region_id, "offset": -1, "length": 0, **fields}


def symbol_for_offset(doc: dict, offset: int) -> dict:
    matches = [region for region in doc["blob"]["regions"]
               if region["offset"] <= offset < region["offset"] + region["length"]]
    if len(matches) != 1:
        raise SystemExit(f"offset 0x{offset:X} belongs to {len(matches)} regions")
    region = matches[0]
    delta = offset - region["offset"]
    return {"to": region["id"], **({"delta": delta} if delta else {})}


def region_at(doc: dict, offset: int, kind: str | None = None) -> dict:
    matches = [region for region in doc["blob"]["regions"]
               if region["offset"] == offset and (kind is None or region["kind"] == kind)]
    if len(matches) != 1:
        raise SystemExit(f"expected one {kind or 'region'} at 0x{offset:X}, found {len(matches)}")
    return matches[0]


def tagged_entries(blob: bytes, offset: int) -> tuple[bool, list[dict]]:
    wide = blob[offset] == 0
    count = blob[offset + 1] if wide else blob[offset]
    stride = 5 if wide else 4
    base = offset + (2 if wide else 1)
    entries = []
    for index in range(count):
        at = base + stride * index
        entry = {
            "tag": blob[at + (1 if wide else 0)],
            "operand": int.from_bytes(blob[at + (2 if wide else 1):at + (4 if wide else 3)],
                                      "little"),
            "opcode": blob[at + (4 if wide else 3)],
        }
        if wide:
            entry["flags"] = blob[at]
        entries.append(entry)
    return wide, entries


def mode_fields(blob: bytes, sections: list[int | None], mode: int) -> dict:
    table = sections[semantics.MODE_SLOT]
    assert table is not None
    entry = ir.u24(blob, table + 3 + 3 * mode) - ir.BASE
    start = ir.u24(blob, entry + 1) - ir.BASE
    pages = []
    for page_index in range(ir.u16(blob, entry + 4)):
        page = ir.u24(blob, entry + 6 + 3 * page_index) - ir.BASE
        pages.append({
            "address": page,
            "list": ir.u24(blob, page) - ir.BASE,
            "program": ir.u24(blob, page + 3) - ir.BASE,
        })
    return {"entry": entry, "start": start, "kind": blob[entry], "pages": pages}


def opaque(region_id: str, data: bytes, *, section: int | None = None) -> dict:
    fields = {"data": hconfig._hex_lines(data)}
    if section is not None:
        fields["section"] = section
    return fresh_region("opaque", region_id, **fields)


def class5_source(blob: bytes, address: int) -> tuple[int, tuple[tuple[int, ...] | None, ...]]:
    """Read every raw pointer slot of one existing arch-9 class-5 record."""
    start = address - ir.BASE - 7
    if (start < 0 or blob[start] != 0 or blob[start + 7] != ir.IR_CLASS_525
            or ir.u24(blob, start + 8) != address - 7):
        raise SystemExit(f"0x{address:06X} is not a self-pointing arch-9 class-5 record")
    groups = blob[start + 11]
    if not 1 <= groups <= 16:
        raise SystemExit(f"class-5 record has unsupported pointer-group count {groups}")
    pointers = [ir.u24(blob, start + 12 + 3 * slot) for slot in range(3 * groups)]
    streams = tuple(None if pointer == 0 else tuple(ir.body(blob, pointer)["words"])
                    for pointer in pointers)
    return ir.u24(blob, start + 1), streams


def build_menu_program(blob: bytes, doc: dict, root: int) -> list[dict]:
    """Keep Devices + Amplifier Genius and all eight row frames."""
    regions = []
    sequence = 0
    for instruction in semantics.screen_program_path(blob, root):
        opcode = instruction["opcode"]
        operands = instruction["operands"]
        region_id = f"clone_menu_screen_{sequence:02d}"
        sequence += 1
        if opcode == 3:
            target = ir.u24(operands, 6) - ir.BASE
            regions.append(fresh_region(
                "screen_picture", region_id,
                coordinates=list(operands[:6]),
                targets=[symbol_for_offset(doc, target)],
            ))
            continue
        if opcode in (4, 5):
            if opcode == 4:
                at = ir.u24(operands, 2) - ir.BASE
                codes = blob[at:blob.index(0, at)]
            else:
                codes = operands[2:-1]
            if codes in MENU_KEEP:
                # Use inline text for every row copy. This removes the only
                # non-picture pointers from the new program.
                regions.append(opaque(
                    region_id,
                    bytes((5, operands[0], operands[1])) + codes + b"\x00",
                ))
            continue
        regions.append(opaque(region_id, bytes((opcode,)) + operands))
    if not regions or regions[0]["id"] != "clone_menu_screen_00":
        raise SystemExit("custom menu screen has no root")
    return regions


def render_map(blob: bytes) -> dict[tuple[int, int], tuple[list[list[int]], list[dict]]]:
    sections = semantics.section_offsets(blob)
    fonts = renderer.fonts(blob, sections)
    result = {}
    for entry in renderer.page_roots(blob, sections):
        try:
            result[(entry["mode"], entry["page"])] = renderer.render_page(
                blob, fonts, entry["root"])
        except Exception as error:
            raise RuntimeError(
                f"render failed for mode {entry['mode']} page {entry['page']} "
                f"at root 0x{entry['root']:X}"
            ) from error
    return result


def clone(source: Path, output: Path, *, place_x96_record_9: bool = False) -> dict:
    if source.resolve() == output.resolve():
        raise SystemExit("refusing to overwrite the source config")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")

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

    placed_source = None
    placed_seed = None
    placed_region = None
    placed_record = None
    if place_x96_record_9:
        source_address = before_groups[X96_GROUP][PLACED_X96_COMMAND]
        period_ns, pointer_streams = class5_source(before_blob, source_address)
        if (len(pointer_streams) != 3 or pointer_streams[0] is None
                or pointer_streams[2] is not None
                or ir.nec_frame(list(pointer_streams[0])) != PLACED_X96_NEC):
            raise SystemExit("X96 command 9 no longer matches the bounded golden record")
        placed_source = {
            "address": source_address,
            "period_ns": period_ns,
            "pointer_streams": pointer_streams,
        }
        placed_seed = encode_class5_record(
            period_ns=period_ns,
            pointer_streams=pointer_streams,
            base_address=CLASS5_LAYOUT_BASE,
        )
        placed_region = opaque("clone_class5_record_9", placed_seed.blob)

    mode_table = before_sections[semantics.MODE_SLOT]
    action_table = before_sections[semantics.ACTION_SLOT]
    ir_table = before_sections[semantics.IR_SLOT]
    assert mode_table is not None and action_table is not None and ir_table is not None
    if ir.u24(before_blob, mode_table) != EXPECTED_MODE_COUNT:
        raise SystemExit("source mode count changed")
    if ir.u16(before_blob, action_table) != EXPECTED_ACTION_COUNT:
        raise SystemExit("source action count changed")

    donor = mode_fields(before_blob, before_sections, DONOR_MODE)
    menu = mode_fields(before_blob, before_sections, MENU_MODE)
    if donor["kind"] != 0 or len(donor["pages"]) != 2 or len(menu["pages"]) != 1:
        raise SystemExit("source donor/menu shape changed")

    action_addresses = ir.action_addresses(before_blob, before_sections)
    donor_lists = [donor["start"]] + [page["list"] for page in donor["pages"]]
    list_fields = [tagged_entries(before_blob, address) for address in donor_lists]
    old_actions = []
    for _wide, entries in list_fields:
        for entry in entries:
            if entry["opcode"] == 0x7F and entry["operand"] not in old_actions:
                old_actions.append(entry["operand"])
    if len(old_actions) != 8:
        raise SystemExit(f"donor reaches {len(old_actions)} action lists, expected eight")

    doc = hconfig.symbolise(hconfig.decompile(raw, source.name))
    regions = doc["blob"]["regions"]
    absolute_sections = [None if offset is None else offset + ir.BASE
                         for offset in before_sections]
    legacy_rows = [offset for offset in range(len(before_blob) - 11)
                   if hconfig.parse_block_header(before_blob, offset, len(before_blob))]
    rooted_pictures = {
        region["offset"] for region in hconfig.arch9_screen_text_regions(
            before_blob, absolute_sections)
        if region["kind"] == "screen_picture"
    }
    if (len(legacy_rows) != 1080 or len(rooted_pictures) != 1114
            or not {offset + 2 for offset in legacy_rows} <= rooted_pictures):
        raise SystemExit("legacy block-header shape no longer closes on rooted screen pictures")
    new_action_regions = []
    action_mapping = {}
    commands = set()
    for position, old_index in enumerate(old_actions):
        instructions = semantics.instructions(before_blob, action_addresses[old_index])
        if (len(instructions) != 2 or instructions[0][0] != 0x7D
                or instructions[1][0] != 0x7C
                or instructions[0][1] >> 8 != DONOR_GROUP
                or instructions[1][1] >> 8 != DONOR_GROUP):
            raise SystemExit(f"donor action {old_index} is not the bounded send/QueueDelay pair")
        command = instructions[0][1] & 0xFF
        commands.add(command)
        new_index = EXPECTED_ACTION_COUNT + position
        action_mapping[old_index] = new_index
        new_action_regions.append(fresh_region(
            "action_list", f"clone_action_{new_index}",
            instructions=[
                {"operand": TARGET_GROUP << 8 | command, "opcode": "0x7D"},
                {"operand": TARGET_GROUP << 8 | (instructions[1][1] & 0xFF),
                 "opcode": "0x7C"},
            ],
        ))
    if commands != set(range(8)):
        raise SystemExit(f"donor commands are {sorted(commands)}, expected 0..7")

    selection_index = EXPECTED_ACTION_COUNT + len(new_action_regions)
    selection_region = fresh_region(
        "action_list", f"clone_action_{selection_index}",
        instructions=[
            {"operand": TARGET_MODE, "opcode": "0x7E"},
            {"operand": 1, "opcode": "0x92"},
        ],
    )
    all_new_actions = new_action_regions + [selection_region]

    def patched_list(region_id: str, source_fields, *, section=None) -> dict:
        wide, entries = source_fields
        changed = copy.deepcopy(entries)
        for entry in changed:
            if entry["opcode"] == 0x7F and entry["operand"] in action_mapping:
                entry["operand"] = action_mapping[entry["operand"]]
        fields = {"wide": wide, "entries": changed}
        if section is not None:
            fields["section"] = section
        return fresh_region("tagged_list", region_id, **fields)

    physical = patched_list("clone_mode_physical", list_fields[0])
    page_lists = [
        patched_list(f"clone_mode_page_list_{index}", list_fields[index + 1], section=8)
        for index in range(2)
    ]
    menu_list = fresh_region(
        "tagged_list", "clone_menu_page_list", section=8, wide=False,
        entries=[{"tag": MENU_TAG, "operand": selection_index, "opcode": 0x7F}],
    )

    group_targets = [symbol_for_offset(doc, address - ir.BASE)
                     for address in before_groups[DONOR_GROUP]]
    if placed_seed is not None and placed_region is not None:
        group_targets[0] = {
            "to": placed_region["id"],
            "delta": placed_seed.record_address - placed_seed.base_address,
        }
    new_group = fresh_region("ir_group", "clone_ir_group", targets=group_targets)

    mode_components = [physical]
    clone_pages = []
    for index, donor_page in enumerate(donor["pages"]):
        jump = fresh_region(
            "reference", f"clone_mode_program_{index}", opcode="0x14",
            targets=[symbol_for_offset(doc, donor_page["program"])],
        )
        page = fresh_region(
            "mode_page", f"clone_mode_page_{index}",
            targets=[{"to": page_lists[index]["id"]}, {"to": jump["id"]}],
        )
        mode_components.extend((jump, page))
        clone_pages.append(page)
    clone_entry = fresh_region(
        "record_header", "clone_mode_entry",
        back_reference={"to": physical["id"]},
        targets=[{"to": page["id"]} for page in clone_pages],
    )
    mode_components.append(clone_entry)

    menu_program = build_menu_program(before_blob, doc, menu["pages"][0]["program"])
    menu_page = fresh_region(
        "mode_page", "clone_menu_page",
        targets=[{"to": menu_list["id"]}, {"to": menu_program[0]["id"]}],
    )

    # Grow the four existing counted structures through symbolic targets.
    ir_table_region = region_at(doc, ir_table, "pointer_table")
    mode_table_region = region_at(doc, mode_table, "pointer_table")
    action_table_region = region_at(doc, action_table, "pointer_table")
    menu_entry_region = region_at(doc, menu["entry"], "record_header")
    ir_table_region["targets"].append({"to": new_group["id"]})
    mode_table_region["targets"].append({"to": clone_entry["id"]})
    action_table_region["targets"].extend({"to": item["id"]} for item in all_new_actions)
    menu_entry_region["targets"].append({"to": menu_page["id"]})

    # Actual page lists belong to base slot 8 and remain one contiguous run.
    section9 = next(index for index, region in enumerate(regions)
                    if region.get("section") == 9)
    regions[section9:section9] = [*page_lists, menu_list]

    # The remaining new records live in the low record area, immediately before
    # base slot 0. This keeps section 17's picture bank and trailer untouched.
    section0 = next(index for index, region in enumerate(regions)
                    if region.get("section") == 0)
    low_regions = [
        *([placed_region] if placed_region is not None else []),
        *all_new_actions,
        new_group,
        *mode_components,
        *menu_program,
        menu_page,
    ]
    regions[section0:section0] = low_regions

    # The class-5 record contains absolute internal u24 pointers. Its byte
    # length is already fixed by the seed encoding, so the normal two-pass
    # hconfig resolver can give us its final base before emission. Re-encoding
    # at that base changes pointer values but cannot change region length.
    if placed_region is not None and placed_seed is not None and placed_source is not None:
        resolve, _ = hconfig._resolver(regions)
        placed_base = ir.BASE + resolve({"to": placed_region["id"]})
        placed_record = encode_class5_record(
            period_ns=placed_source["period_ns"],
            pointer_streams=placed_source["pointer_streams"],
            base_address=placed_base,
        )
        if (len(placed_record.blob) != len(placed_seed.blob)
                or placed_record.record_address - placed_record.base_address
                != placed_seed.record_address - placed_seed.base_address):
            raise SystemExit("relocating class-5 internals changed its fixed layout")
        decoded = decode_class5_record(
            placed_record.blob,
            base_address=placed_record.base_address,
            record_address=placed_record.record_address,
        )
        if (decoded.period_ns != placed_source["period_ns"]
                or decoded.pointer_streams != placed_source["pointer_streams"]):
            raise SystemExit("local decoder did not recover the relocated class-5 record")
        placed_region["data"] = hconfig._hex_lines(placed_record.blob)

    candidate = hconfig.compile_config(doc)
    after_blob = hconfig.split_container(candidate)[2]
    after_sections = semantics.section_offsets(after_blob)
    after_groups = ir.ir_groups(after_blob, after_sections)
    if [len(group) for group in after_groups] != [*EXPECTED_GROUP_SIZES, 8]:
        raise SystemExit("cloned IR group did not close")
    after_original_ir_semantics = [
        [class5_source(after_blob, address) for address in group]
        for group in after_groups[:len(before_groups)]
    ]
    if after_original_ir_semantics != before_ir_semantics:
        raise SystemExit("one or more original class-5 records changed after relocation")
    if placed_record is None:
        if after_groups[TARGET_GROUP] != after_groups[DONOR_GROUP]:
            raise SystemExit("cloned IR group does not share the donor's exact records")
    else:
        if (after_groups[TARGET_GROUP][0] != placed_record.record_address
                or after_groups[TARGET_GROUP][0] == after_groups[DONOR_GROUP][0]
                or after_groups[TARGET_GROUP][1:] != after_groups[DONOR_GROUP][1:]):
            raise SystemExit("group 4 does not contain one placed record plus seven shared records")
        at = placed_record.base_address - ir.BASE
        if after_blob[at:at + len(placed_record.blob)] != placed_record.blob:
            raise SystemExit("placed class-5 bytes differ after config compilation")
        placed_period, placed_streams = class5_source(
            after_blob, after_groups[TARGET_GROUP][0])
        if (placed_period != placed_source["period_ns"]
                or placed_streams != placed_source["pointer_streams"]
                or ir.nec_frame(list(placed_streams[0])) != PLACED_X96_NEC):
            raise SystemExit("placed class-5 record does not expand to the X96 golden stream")

    target_bindings = ir.mode_bindings(after_blob, after_sections, TARGET_MODE)
    if ({binding["group"] for binding in target_bindings} != {TARGET_GROUP}
            or {binding["command"] for binding in target_bindings} != set(range(8))
            or len(target_bindings) != 8):
        raise SystemExit("cloned mode does not close on all eight group-4 commands")

    after_menu = mode_fields(after_blob, after_sections, MENU_MODE)
    if len(after_menu["pages"]) != 2:
        raise SystemExit("Devices menu did not gain exactly one page")
    menu_binding = semantics.tagged_instructions(after_blob, after_menu["pages"][1]["list"])
    after_actions = ir.action_addresses(after_blob, after_sections)
    if menu_binding != [(MENU_TAG, 0x7F, selection_index)]:
        raise SystemExit("new Devices page binding changed shape")
    if semantics.instructions(after_blob, after_actions[selection_index]) != [(0x7E, TARGET_MODE),
                                                                              (0x92, 1)]:
        raise SystemExit("new Devices selection action changed shape")

    before_render = render_map(before_blob)
    after_render = render_map(after_blob)
    if len(before_render) != 135 or len(after_render) != 138:
        raise SystemExit("unexpected screen population")
    changed_existing = [key for key, value in before_render.items()
                        if after_render.get(key, (None,))[0] != value[0]]
    if changed_existing:
        raise SystemExit(f"existing screens changed: {changed_existing}")
    for page in range(2):
        if after_render[(TARGET_MODE, page)][0] != after_render[(DONOR_MODE, page)][0]:
            raise SystemExit(f"clone screen page {page} differs from donor")
    menu_codes = {bytes(item["codes"]) for item in after_render[(MENU_MODE, 1)][1]}
    if menu_codes != MENU_KEEP:
        raise SystemExit(f"new menu page draws unexpected strings: {menu_codes}")

    slot8 = semantics.verify_slot8(after_blob, after_sections)
    actions_check = semantics.verify_action_closures(after_blob, after_sections)
    screens_check = semantics.verify_screen_programs(after_blob, after_sections)
    rebuilt = hconfig.compile_config(hconfig.decompile(candidate, output.name))
    if rebuilt != candidate:
        raise SystemExit(f"candidate round trip differs at {hconfig.first_difference(candidate, rebuilt)}")
    stored_trailer = int.from_bytes(after_blob[-6:-4], "little")
    if stored_trailer != hconfig.trailer_checksum(after_blob):
        raise SystemExit("firmware trailer checksum is invalid")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(candidate)
    proof = {
        "scope": ("offline fifth-device plus placed class-5 proof; never hardware-tested"
                  if placed_record else
                  "offline structural fifth-device proof; never hardware-tested"),
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_sha256": sha256(raw),
        "output_sha256": sha256(candidate),
        "blob_size_before": len(before_blob),
        "blob_size_after": len(after_blob),
        "blob_growth": len(after_blob) - len(before_blob),
        "donor": {"name": "Amplifier Genius", "ir_group": DONOR_GROUP, "mode": DONOR_MODE},
        "clone": {"display_name": "Amplifier Genius", "ir_group": TARGET_GROUP,
                  "mode": TARGET_MODE, "records": 8, "bindings": 8, "pages": 2},
        "action_index_mapping": {str(old): new for old, new in action_mapping.items()},
        "selection_action": selection_index,
        "ir_group_sizes": [len(group) for group in after_groups],
        "mode_count": ir.u24(after_blob, after_sections[semantics.MODE_SLOT]),
        "action_count": ir.u16(after_blob, after_sections[semantics.ACTION_SLOT]),
        "screen_count": len(after_render),
        "slot8": slot8,
        "actions": actions_check,
        "screens": screens_check,
        "checks": {
            "shared_ir_records_exact": placed_record is None,
            "one_generated_class5_record_placed": placed_record is not None,
            "remaining_seven_ir_records_shared": (placed_record is not None),
            "placed_class5_local_decode_exact": (placed_record is not None),
            "all_original_class5_records_expand_exactly": True,
            "all_eight_clone_commands_close": True,
            "existing_135_screens_pixel_identical": True,
            "clone_pages_pixel_identical_to_donor": True,
            "new_menu_page_has_only_devices_and_donor_name": True,
            "firmware_trailer_checksum": True,
            "ezhex_checksum": hconfig.blob_checksum(after_blob),
            "semantic_round_trip_byte_identical": True,
        },
        "screen_row_reclassification": {
            "legacy_twelve_byte_shape_matches": len(legacy_rows),
            "rooted_opcode_3_picture_draws": len(rooted_pictures),
            "additional_picture_draws_without_the_row_prefix": (
                len(rooted_pictures) - len(legacy_rows)),
            "every_legacy_match_is_opcode_22_followed_by_opcode_3": True,
            "remaining_block_header_regions_after_rooted_overlay": sum(
                region["kind"] == "block_header" for region in regions),
        },
        "known_limitations": [
            "not written to or accepted by Harmony hardware",
            ("the clone keeps the donor visible name and seven donor IR records"
             if placed_record else
             "the clone deliberately shares the donor IR records and visible name"),
            "the unused compiler-era duplicate of each new page list is not emitted",
            "no new state-variable name or device identifier is invented",
            "architecture 9 / Harmony 525 only",
        ],
        "credit": {
            "device_is_ir_group_and_mode_reader": "Danny Bloemendaal / harmony-explorations",
            "class5_structure_and_independent_reader": "Danny Bloemendaal / harmony-explorations",
            "minimal_clone_layout_and_proof": "trelowney Harmony project",
            "literal_class5_packer_and_placement_proof": "trelowney Harmony project",
        },
    }
    if placed_record is not None and placed_source is not None:
        proof["checks"].pop("shared_ir_records_exact")
        proof["placed_class5"] = {
            "source_group": X96_GROUP,
            "source_command": PLACED_X96_COMMAND,
            "source_record_address": placed_source["address"],
            "target_group": TARGET_GROUP,
            "target_command": 0,
            "expected_nec": list(PLACED_X96_NEC),
            "expected_stream_word_counts": [
                None if stream is None else len(stream)
                for stream in placed_source["pointer_streams"]
            ],
            "blob_sha256": sha256(placed_record.blob),
            "manifest": placed_record.manifest(),
        }
    else:
        proof["checks"].pop("one_generated_class5_record_placed")
        proof["checks"].pop("remaining_seven_ir_records_shared")
        proof["checks"].pop("placed_class5_local_decode_exact")
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_EZHEX)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument(
        "--place-x96-record-9", action="store_true",
        help="replace clone command 0 with a newly packed copy of X96 record 9",
    )
    args = parser.parse_args()
    if args.proof and args.proof.exists():
        raise SystemExit(f"refusing to overwrite existing proof: {args.proof}")
    proof = clone(args.config, args.out, place_x96_record_9=args.place_x96_record_9)
    if args.proof:
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("PASS: cloned Amplifier Genius as IR group 4 / mode 114")
    if args.place_x96_record_9:
        print("  command 0 is a newly placed class-5 copy of X96 record 9")
    print(f"  {proof['ir_group_sizes']}, {proof['mode_count']} modes, "
          f"{proof['action_count']} actions, {proof['screen_count']} screens")
    print(f"  grew by {proof['blob_growth']} bytes; existing 135 screens unchanged")
    print(args.out.resolve())
    if args.proof:
        print(args.proof.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
