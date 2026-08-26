"""Append a real seventh TV Panasonic LCD page to the public Harmony 525.

This is an offline architecture-9 authoring proof.  It appends glyph ``7`` as
a genuinely new font-local code, rebuilds the six existing TV page programs so
their footer reads ``N of 7``, and appends a seventh page reading ``7 of 7``.
The new page deliberately duplicates the coherent I/II / Pwr On / Pwr Off /
Pwr Toggle page-3 visual and bindings; the point of this bounded proof is page,
screen-program and glyph allocation, not inventing another command meaning.

Nothing here opens USB hardware.  The output must not be written to a remote
without a separate review and recovery-backed hardware plan.
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
import h525_lcd_author as lcd
import render_525_screens as renderer
import verify_525_semantics as semantics


MODE = 78
SOURCE_PAGE_FOR_NEW = 3
FOOTER_FONT = 4
NEW_GLYPH_CODE = 67  # Existing global alphabet occupies codes 1..66.
EXPECTED_PAGE_COUNT = 6
EXPECTED_LIVE_PAGES = 135
EXPECTED_DIGIT_7_SHAPE_KEY = "8:7e61864cab4a327a"


DIGIT_7 = (
    "......",
    ".####.",
    "....#.",
    "....#.",
    "...#..",
    "...#..",
    "......",
    "......",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def fresh_region(kind: str, region_id: str, **fields) -> dict:
    return {"kind": kind, "id": region_id, "offset": -1, "length": 0, **fields}


def opaque(region_id: str, data: bytes, *, section: int | None = None) -> dict:
    fields = {"data": hconfig._hex_lines(data)}
    if section is not None:
        fields["section"] = section
    return fresh_region("opaque", region_id, **fields)


def symbol_for_offset(doc: dict, offset: int) -> dict:
    matches = [
        region for region in doc["blob"]["regions"]
        if region["offset"] <= offset < region["offset"] + region["length"]
    ]
    if len(matches) != 1:
        raise SystemExit(f"offset 0x{offset:X} belongs to {len(matches)} regions")
    region = matches[0]
    delta = offset - region["offset"]
    return {"to": region["id"], **({"delta": delta} if delta else {})}


def region_at(doc: dict, offset: int, kind: str | None = None) -> dict:
    matches = [
        region for region in doc["blob"]["regions"]
        if region["offset"] == offset and (kind is None or region["kind"] == kind)
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one {kind or 'region'} at 0x{offset:X}, found {len(matches)}")
    return matches[0]


def mode_fields(blob: bytes, sections: list[int | None]) -> dict:
    table = sections[semantics.MODE_SLOT]
    assert table is not None
    entry = ir.u24(blob, table + 3 + 3 * MODE) - ir.BASE
    start = ir.u24(blob, entry + 1) - ir.BASE
    pages = []
    for index in range(ir.u16(blob, entry + 4)):
        record = ir.u24(blob, entry + 6 + 3 * index) - ir.BASE
        pages.append({
            "index": index,
            "record": record,
            "list": ir.u24(blob, record) - ir.BASE,
            "program": ir.u24(blob, record + 3) - ir.BASE,
        })
    return {"entry": entry, "physical": start, "pages": pages}


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
            "operand": int.from_bytes(
                blob[at + (2 if wide else 1):at + (4 if wide else 3)], "little"
            ),
            "opcode": blob[at + (4 if wide else 3)],
        }
        if wide:
            entry["flags"] = blob[at]
        entries.append(entry)
    return wide, entries


def text_codes(blob: bytes, opcode: int, operands: bytes) -> bytes:
    if opcode == 5:
        return operands[2:-1]
    at = ir.u24(operands, 2) - ir.BASE
    return blob[at:blob.index(0, at)]


def clone_program_with_footer(
    blob: bytes,
    doc: dict,
    root: int,
    region_prefix: str,
    page_number_code: int,
) -> list[dict]:
    """Flatten one rooted screen path and replace its footer total with 7."""
    regions = []
    current_font = 0
    sequence = 0
    page_digits = 0
    total_digits = 0
    for instruction in semantics.screen_program_path(blob, root):
        opcode = instruction["opcode"]
        operands = instruction["operands"]
        if opcode == 20:
            # Flatten the followed path; retaining this jump would bypass the
            # newly copied continuation regions.
            continue
        region_id = f"{region_prefix}_{sequence:03d}"
        sequence += 1
        if opcode == 3:
            target = ir.u24(operands, 6) - ir.BASE
            regions.append(fresh_region(
                "screen_picture",
                region_id,
                coordinates=list(operands[:6]),
                targets=[symbol_for_offset(doc, target)],
            ))
            continue
        if opcode == 16:
            current_font = operands[0]
            regions.append(opaque(region_id, bytes((opcode,)) + operands))
            continue
        if opcode in (4, 5):
            x, y = operands[:2]
            codes = text_codes(blob, opcode, operands)
            if current_font == FOOTER_FONT and y == 56 and len(codes) == 1:
                if x <= 40:
                    if page_number_code == NEW_GLYPH_CODE:
                        x = 35  # wide page-number footer slot
                    codes = bytes((page_number_code,))
                    page_digits += 1
                elif x >= 60:
                    x = 65
                    codes = bytes((NEW_GLYPH_CODE,))
                    total_digits += 1
            # Inline every copied string so the new program has no text
            # pointers and can be relocated independently.
            regions.append(opaque(region_id, bytes((5, x, y)) + codes + b"\x00"))
            continue
        regions.append(opaque(region_id, bytes((opcode,)) + operands))
    if not regions or page_digits != 1 or total_digits != 1:
        raise SystemExit(
            f"{region_prefix}: footer closure page={page_digits}, total={total_digits}"
        )
    if hconfig._unhex(regions[-1]["data"]) != b"\x00":
        raise SystemExit(f"{region_prefix}: flattened program does not end")
    return regions


def render_map(blob: bytes) -> dict[tuple[int, int], tuple[list[list[int]], list[dict]]]:
    sections = semantics.section_offsets(blob)
    fonts = renderer.fonts(blob, sections)
    result = {}
    for entry in renderer.page_roots(blob, sections):
        result[(entry["mode"], entry["page"])] = renderer.render_page(
            blob, fonts, entry["root"]
        )
    return result


def expanded_ir_semantics(blob: bytes, sections: list[int | None]) -> list[list[dict]]:
    """Expand every raw class-5 pointer slot, retaining significant NULLs."""
    result = []
    for group in ir.ir_groups(blob, sections):
        records = []
        for address in group:
            start = address - ir.BASE - 7
            assert blob[start] == 0 and blob[start + 7] == ir.IR_CLASS_525
            pointer_groups = blob[start + 11]
            pointers = [
                ir.u24(blob, start + 12 + 3 * slot)
                for slot in range(3 * pointer_groups)
            ]
            records.append({
                "period_ns": ir.u24(blob, start + 1),
                "on_ns": ir.u24(blob, start + 4),
                "streams": [
                    None if pointer == 0 else tuple(ir.body(blob, pointer)["words"])
                    for pointer in pointers
                ],
            })
        result.append(records)
    return result


def changed_pixels(before: list[list[int]], after: list[list[int]]) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y, (before_row, after_row) in enumerate(zip(before, after))
        for x, (left, right) in enumerate(zip(before_row, after_row))
        if left != right
    }


def build(source: Path, output: Path, *, overwrite: bool = False) -> dict:
    if source.resolve() == output.resolve():
        raise SystemExit("refusing to overwrite source")
    if output.exists() and not overwrite:
        raise SystemExit(f"refusing to overwrite existing output: {output}")

    raw = source.read_bytes()
    before = hconfig.split_container(raw)[2]
    sections = semantics.section_offsets(before)
    mode = mode_fields(before, sections)
    if len(mode["pages"]) != EXPECTED_PAGE_COUNT:
        raise SystemExit("TV page count changed")

    before_render = render_map(before)
    if len(before_render) != EXPECTED_LIVE_PAGES:
        raise SystemExit("unexpected original live-page count")
    before_ir_semantics = expanded_ir_semantics(before, sections)
    before_fonts = renderer.fonts(before, sections)
    existing_max_code = max(font.first + font.count - 1 for font in before_fonts)
    if NEW_GLYPH_CODE != existing_max_code + 1:
        raise SystemExit(
            f"new glyph code {NEW_GLYPH_CODE} does not follow existing alphabet through "
            f"{existing_max_code}"
        )
    if lcd.glyph_shape_key(DIGIT_7) != EXPECTED_DIGIT_7_SHAPE_KEY:
        raise SystemExit("digit 7 bitmap no longer matches the public H525 typeface oracle")

    doc = hconfig.symbolise(hconfig.decompile(raw, source.name))
    regions = doc["blob"]["regions"]

    # Codes are one shared generated alphabet even when a particular font set
    # leaves one of them NULL.  Code 63, for example, is '#' in font 0, so
    # reusing font 4's NULL code 63 for '7' would make one code mean two
    # characters in the same container.  Grow only font 4 from codes 1..66 to
    # 1..67 and give the new character a genuinely new code instead.
    font_table = sections[7]
    assert font_table is not None
    font4_address = ir.u24(before, font_table + 2 + 3 * FOOTER_FONT) - ir.BASE
    font4 = region_at(doc, font4_address, "font_set")
    alphabet = lcd.alphabet_map()
    allocated_code, glyph = lcd.install_character_glyph(
        before,
        doc,
        sections,
        font_index=FOOTER_FONT,
        character="7",
        rows=DIGIT_7,
        alphabet=alphabet,
        region_id="tv_footer_glyph_7",
    )
    if allocated_code != NEW_GLYPH_CODE:
        raise SystemExit(f"expected new glyph code {NEW_GLYPH_CODE}, got {allocated_code}")

    # Build seven independent flattened programs. Pages 0..5 retain their
    # original page-number glyph; only the total becomes 7. Page 6 clones page
    # 3's coherent power page and uses glyph 7 for both numbers.
    programs = []
    existing_program_roots = []
    for page in mode["pages"]:
        page_program = clone_program_with_footer(
            before,
            doc,
            page["program"],
            f"tv_page_{page['index']}_of_7",
            # Preserve the original page-number draw by taking its existing
            # single code from the source path; resolved below.
            page_number_code=next(
                text_codes(before, item["opcode"], item["operands"])[0]
                for item in semantics.screen_program_path(before, page["program"])
                if item["opcode"] in (4, 5)
                and item["operands"][1] == 56
                and item["operands"][0] <= 40
                and len(text_codes(before, item["opcode"], item["operands"])) == 1
            ),
        )
        programs.extend(page_program)
        existing_program_roots.append({"to": page_program[0]["id"]})

    new_program = clone_program_with_footer(
        before,
        doc,
        mode["pages"][SOURCE_PAGE_FOR_NEW]["program"],
        "tv_page_6_of_7",
        page_number_code=NEW_GLYPH_CODE,
    )
    programs.extend(new_program)

    # Retarget the six existing page records to the corrected N-of-7 programs.
    for page, target in zip(mode["pages"], existing_program_roots):
        page_region = region_at(doc, page["record"], "mode_page")
        page_region["targets"][1] = target

    # The seventh page uses a fresh slot-8 list copied from page 3 and a fresh
    # six-byte mode-page record pointing to the new program.
    source_wide, source_entries = tagged_entries(
        before, mode["pages"][SOURCE_PAGE_FOR_NEW]["list"]
    )
    new_list = fresh_region(
        "tagged_list",
        "tv_page_6_list",
        section=8,
        wide=source_wide,
        entries=copy.deepcopy(source_entries),
    )
    new_page = fresh_region(
        "mode_page",
        "tv_page_6_record",
        targets=[{"to": new_list["id"]}, {"to": new_program[0]["id"]}],
    )
    mode_entry = region_at(doc, mode["entry"], "record_header")
    mode_entry["targets"].append({"to": new_page["id"]})

    # Keep actual page lists in the contiguous slot-8 run.
    section9 = next(index for index, region in enumerate(regions) if region.get("section") == 9)
    regions[section9:section9] = [new_list]

    # New unsectioned objects live before slot 0; hconfig relinks all known
    # absolute pointers, including fonts, screen pictures and class-5 graphs.
    section0 = next(index for index, region in enumerate(regions) if region.get("section") == 0)
    regions[section0:section0] = [glyph, *programs, new_page]

    candidate = hconfig.compile_config(doc)
    after = hconfig.split_container(candidate)[2]
    after_sections = semantics.section_offsets(after)
    after_mode = mode_fields(after, after_sections)
    if len(after_mode["pages"]) != 7:
        raise SystemExit("candidate does not contain seven TV pages")

    # Full semantic and round-trip closure.
    slot8 = semantics.verify_slot8(after, after_sections)
    actions = semantics.verify_action_closures(after, after_sections)
    screens = semantics.verify_screen_programs(after, after_sections)
    rebuilt = hconfig.compile_config(hconfig.decompile(candidate, output.name))
    if rebuilt != candidate:
        raise SystemExit(f"candidate round trip differs at {hconfig.first_difference(candidate, rebuilt)}")

    after_render = render_map(after)
    if len(after_render) != EXPECTED_LIVE_PAGES + 1:
        raise SystemExit("candidate live-page count is not 136")
    changed_non_tv = [
        key for key, value in before_render.items()
        if key[0] != MODE and after_render.get(key, (None,))[0] != value[0]
    ]
    if changed_non_tv:
        raise SystemExit(f"non-TV screens changed: {changed_non_tv}")

    # New footer glyph must decode exactly as authored.
    after_fonts = renderer.fonts(after, after_sections)
    if [font.count for font in after_fonts] != [66, 66, 66, 66, 67]:
        raise SystemExit("font counts changed outside the intended font-4 append")
    decoded_seven = after_fonts[FOOTER_FONT].glyph(NEW_GLYPH_CODE)
    expected_seven = [
        [renderer.INK if char == "#" else renderer.PAPER for char in row]
        for row in DIGIT_7
    ]
    if decoded_seven != expected_seven:
        raise SystemExit("new footer glyph does not decode exactly")

    # All seven pages must visibly contain the new code as the total. The last page
    # uses it twice, for its page number and total.
    footer_counts = []
    for page_index in range(7):
        strings = after_render[(MODE, page_index)][1]
        count = sum(
            item["font"] == FOOTER_FONT and item["y"] == 56
            and NEW_GLYPH_CODE in item["codes"]
            for item in strings
        )
        footer_counts.append(count)
    if footer_counts != [1, 1, 1, 1, 1, 1, 2]:
        raise SystemExit(f"unexpected footer-7 population: {footer_counts}")

    # Existing TV pages may differ only in the total digit's 7x8 footer box.
    footer_total_box = {(x, y) for y in range(56, 64) for x in range(65, 72)}
    existing_tv_pixel_changes = []
    for page_index in range(6):
        changed = changed_pixels(
            before_render[(MODE, page_index)][0], after_render[(MODE, page_index)][0]
        )
        if not changed or not changed <= footer_total_box:
            raise SystemExit(
                f"TV page {page_index} changed outside total footer: {sorted(changed - footer_total_box)}"
            )
        existing_tv_pixel_changes.append(len(changed))

    # The seventh page copies page 3 and may differ only in its page-number and
    # total digit boxes. Everything else, including command text, is identical.
    footer_page_box = {(x, y) for y in range(56, 64) for x in range(35, 42)}
    new_page_changed = changed_pixels(
        before_render[(MODE, SOURCE_PAGE_FOR_NEW)][0], after_render[(MODE, 6)][0]
    )
    if not new_page_changed or not new_page_changed <= footer_total_box | footer_page_box:
        raise SystemExit("new page differs from source outside its two footer digits")

    # Page 6 copied page 3's action semantics exactly.
    bindings = ir.mode_bindings(after, after_sections, MODE)
    by_page = {
        page: sorted(
            (item["tag"], item["group"], item["command"])
            for item in bindings if item["source"] == "lcd" and item["page"] == page
        )
        for page in range(7)
    }
    if by_page[6] != by_page[SOURCE_PAGE_FOR_NEW]:
        raise SystemExit("new page bindings differ from source power page")

    # Existing IR record addresses and expanded semantics are byte-source
    # independent of this page edit; exact address equality proves no group was
    # touched while the relinker moved its internal targets safely.
    before_groups = ir.ir_groups(before, sections)
    after_groups = ir.ir_groups(after, after_sections)
    if before_groups != after_groups:
        raise SystemExit("IR group record addresses changed")
    if expanded_ir_semantics(after, after_sections) != before_ir_semantics:
        raise SystemExit("one or more expanded class-5 IR records changed")

    physical_start = 0x820000
    physical_end = physical_start + len(after)
    event_journal_start = 0x870000
    if physical_end >= 0x840000 or physical_end >= event_journal_start:
        raise SystemExit("candidate crossed the bounded two-sector capacity envelope")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(candidate)
    return {
        "scope": "offline architecture-9 LCD page/glyph authoring proof; never hardware-tested",
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_sha256": sha256(raw),
        "output_sha256": sha256(candidate),
        "blob_size_before": len(before),
        "blob_size_after": len(after),
        "blob_growth": len(after) - len(before),
        "mode": MODE,
        "pages_before": 6,
        "pages_after": 7,
        "live_pages_before": len(before_render),
        "live_pages_after": len(after_render),
        "new_page_copies_bindings_from": SOURCE_PAGE_FOR_NEW,
        "new_glyph": {
            "font": FOOTER_FONT,
            "code": NEW_GLYPH_CODE,
            "appended_after_existing_alphabet": True,
            "previous_font_count": NEW_GLYPH_CODE - font4["first"],
            "rows": list(DIGIT_7),
            "shape_key": lcd.glyph_shape_key(DIGIT_7),
            "matches_public_h525_typeface_oracle": True,
            "encoded_sha256": sha256(lcd.encode_literal_glyph(DIGIT_7)),
        },
        "footer_new_glyph_occurrences_by_page": footer_counts,
        "existing_tv_footer_changed_pixels": existing_tv_pixel_changes,
        "new_page_footer_changed_pixels": len(new_page_changed),
        "new_page_bindings": [
            {"tag": f"0x{tag:02X}", "group": group, "command": command}
            for tag, group, command in by_page[6]
        ],
        "slot8": slot8,
        "actions": actions,
        "screens": screens,
        "checks": {
            "new_glyph_decodes_pixel_exactly": True,
            "new_glyph_code_follows_existing_global_alphabet": True,
            "only_footer_font_grew_from_66_to_67_codes": True,
            "all_six_existing_tv_pages_rebuilt_with_total_7": True,
            "new_page_renders_7_of_7": True,
            "all_129_non_tv_pages_pixel_identical": True,
            "new_page_binding_semantics_equal_source_page_3": True,
            "ir_group_record_addresses_unchanged": True,
            "all_200_expanded_class5_records_exact": True,
            "semantic_round_trip_byte_identical": True,
            "firmware_trailer_checksum": True,
            "ezhex_checksum": hconfig.blob_checksum(after),
            "physical_config_start": f"0x{physical_start:06X}",
            "physical_config_end": f"0x{physical_end:06X}",
            "event_journal_margin": event_journal_start - physical_end,
            "same_two_64k_erase_sectors_as_source": True,
        },
        "limitations": [
            "not written to or accepted by Harmony hardware",
            "new page deliberately duplicates the existing power page commands",
            "one new digit glyph is proven; arbitrary text layout remains a later generalisation",
            "architecture 9 / Harmony 525 only",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_EZHEX)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.proof and args.proof.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing proof: {args.proof}")
    proof = build(args.config, args.out, overwrite=args.force)
    if args.proof:
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("PASS TV Panasonic mode 78: 6 -> 7 LCD pages")
    print("PASS new footer glyph 7 decodes pixel-exactly at appended code 67")
    print("PASS all old TV footers read N of 7 and new page reads 7 of 7")
    print("PASS 129 non-TV pages pixel-identical; all IR record addresses unchanged")
    print(f"PASS symbolic round trip and checksums: {proof['output_sha256']}")
    print(args.out.resolve())
    if args.proof:
        print(args.proof.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
