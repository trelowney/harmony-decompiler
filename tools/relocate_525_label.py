"""Append and retarget one longer Harmony 525 display label, offline only.

Unlike ``edit_525_label.py``, this experiment permits a longer glyph string. It
does not shift any existing payload: the new terminated string is inserted just
before the blob footer, all opcode-4 users are retargeted, and the one inline
opcode-5 copy is rewritten as an opcode-4 draw followed by an opcode-20 jump to
its original continuation. The old instruction keeps exactly the same extent.

The output is still an experiment, not a remote-safe configuration. The tool
never opens hardware and refuses to overwrite any file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import _paths
import hconfig
import render_525_screens as renderer
import verify_525_semantics as semantics
from edit_525_label import (EXPECTED_TITLE_USERS, SOURCE_CODES, SOURCE_LABEL,
                            encode_title, rendered_screens)

EXPECTED_RASTER_CHANGES = (
    (45, 0),
    (100, 0),
    *((111, page) for page in range(6)),
)


def section_pointers(blob: bytes):
    marker = blob.index(hconfig.HEADER_MARKER)
    pointers = [int.from_bytes(blob[offset:offset + 4], "little") or None
                for offset in range(hconfig.PTR_TABLE_OFF, marker, 4)]
    while pointers and pointers[-1] is None:
        pointers.pop()
    return pointers


def screen_text_regions(blob: bytes):
    return hconfig.arch9_screen_text_regions(blob, section_pointers(blob))


def source_inline_instruction(blob: bytes) -> dict:
    sections = semantics.section_offsets(blob)
    matches = []
    for entry in renderer.page_roots(blob, sections):
        for instruction in semantics.screen_program_path(blob, entry["root"]):
            if (instruction["opcode"] == 5
                    and instruction["operands"][2:-1] == SOURCE_CODES):
                matches.append(instruction)
    unique = {instruction["offset"]: instruction for instruction in matches}
    if len(unique) != 1:
        raise SystemExit(f"found {len(unique)} inline source-label instructions, expected one")
    return next(iter(unique.values()))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wrap_container(source: bytes, blob: bytes) -> bytes:
    xml, separator, _old_blob = hconfig.split_container(source)
    if xml is None:
        return blob
    xml = hconfig._set_tag(xml, "BINARYDATASIZE", len(blob))
    xml = hconfig._set_tag(xml, "CHECKSUM", hconfig.blob_checksum(blob))
    return xml + separator + blob


def relocate(source: Path, output: Path, new_label: str) -> dict:
    if source.resolve() == output.resolve():
        raise SystemExit("refusing to overwrite the source config")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")

    new_codes = encode_title(new_label)
    if len(new_codes) <= len(SOURCE_CODES):
        raise SystemExit("this experiment requires a label longer than X96 Box")

    raw = source.read_bytes()
    before_blob = hconfig.split_container(raw)[2]
    if before_blob[-4:] != hconfig.END_MAGIC:
        raise SystemExit("expected the arch-9 footer at the end of the blob")

    source_offset = before_blob.find(SOURCE_CODES)
    if source_offset < 0 or before_blob.find(SOURCE_CODES, source_offset + 1) >= 0:
        raise SystemExit("source glyph string is not unique")

    references = [region for region in screen_text_regions(before_blob)
                  if region["kind"] == "screen_reference"
                  and region["targets"] == [source_offset]]
    if len(references) != 24:
        raise SystemExit(f"found {len(references)} external source-label users, expected 24")

    inline = source_inline_instruction(before_blob)
    inline_length = 1 + len(inline["operands"])
    if inline_length != 11:
        raise SystemExit(f"source inline instruction is {inline_length} bytes, expected 11")
    continuation = inline["offset"] + inline_length
    x, y = inline["operands"][:2]

    # The final six bytes are `<u16 firmware checksum> <MCHA>`. Append before
    # the checksum, not merely before the marker, so the validator still finds
    # the stored word at exactly `len(blob) - 6`.
    trailer = len(before_blob) - hconfig.TRAILER_CHECKSUM_OFFSET
    footer = len(before_blob) - 4
    new_string = new_codes + b"\x00"
    new_address = trailer + hconfig.CONFIG_BASE
    pointer = new_address.to_bytes(3, "little")

    patched = bytearray(before_blob)
    for reference in references:
        start = reference["offset"]
        if patched[start] != 4:
            raise SystemExit(f"opcode-4 reference disappeared at 0x{start:X}")
        patched[start + 3:start + 6] = pointer

    # Six bytes draw the new external string. Four bytes jump over the one
    # remaining byte to the instruction that already followed the old inline
    # string. The last byte is an unreachable zero, preserving the original
    # eleven-byte extent without inventing a screen no-op.
    replacement = (bytes((4, x, y)) + pointer + bytes((20,))
                   + (continuation + hconfig.CONFIG_BASE).to_bytes(3, "little")
                   + b"\x00")
    if len(replacement) != inline_length:
        raise SystemExit("inline replacement does not preserve its extent")
    patched[inline["offset"]:continuation] = replacement

    # Only the footer moves. Every existing section and payload keeps its old
    # offset; the last section simply gains the new terminated glyph string.
    after_blob = bytes(patched[:trailer] + new_string + patched[trailer:])
    new_footer = footer + len(new_string)
    mutable = bytearray(after_blob)
    mutable[4:8] = (new_footer + hconfig.CONFIG_BASE).to_bytes(4, "little")
    checksum_at = len(mutable) - hconfig.TRAILER_CHECKSUM_OFFSET
    mutable[checksum_at:checksum_at + 2] = hconfig.trailer_checksum(mutable).to_bytes(2, "little")
    after_blob = bytes(mutable)

    candidate = wrap_container(raw, after_blob)
    doc = hconfig.symbolise(hconfig.decompile(candidate, output.name))
    rebuilt = hconfig.compile_config(doc)
    if rebuilt != candidate:
        difference = hconfig.first_difference(candidate, rebuilt)
        raise SystemExit(f"semantic rebuild differs at file offset 0x{difference:X}")

    rebuilt_blob = hconfig.split_container(rebuilt)[2]
    stored_trailer = int.from_bytes(rebuilt_blob[-6:-4], "little")
    computed_trailer = hconfig.trailer_checksum(rebuilt_blob)
    if stored_trailer != computed_trailer:
        raise SystemExit("relocated output has an invalid firmware trailer checksum")

    new_regions = screen_text_regions(after_blob)
    new_references = [region for region in new_regions
                      if region["kind"] == "screen_reference"
                      and region["targets"] == [trailer]]
    old_references = [region for region in new_regions
                      if region["kind"] == "screen_reference"
                      and region["targets"] == [source_offset]]
    if len(new_references) != 25 or old_references:
        raise SystemExit(
            f"retarget closure failed: new={len(new_references)}, old={len(old_references)}")
    glyph = [region for region in new_regions
             if region["kind"] == "glyph_string" and region["offset"] == trailer]
    if len(glyph) != 1 or bytes(glyph[0]["codes"]) != new_codes:
        raise SystemExit("appended glyph string was not recovered semantically")

    symbolic_glyph = next(region for region in doc["blob"]["regions"]
                          if region["kind"] == "glyph_string"
                          and region["codes"] == list(new_codes))
    symbolic_users = [region for region in doc["blob"]["regions"]
                      if region["kind"] == "screen_reference"
                      and region["targets"] == [{"to": symbolic_glyph["id"]}]]
    if len(symbolic_users) != 25:
        raise SystemExit(f"only {len(symbolic_users)} symbolic users reach the new string")

    before_pages = rendered_screens(before_blob)
    after_pages = rendered_screens(after_blob)
    changed_screens = sorted((before[0]["mode"], before[0]["page"])
                             for before, after in zip(before_pages, after_pages)
                             if before[1] != after[1])
    if changed_screens != list(EXPECTED_RASTER_CHANGES):
        raise SystemExit(f"unexpected rendered screen changes: {changed_screens}")
    target_users = sorted({(entry["mode"], entry["page"])
                           for entry, _canvas, strings in after_pages
                           if any(bytes(item["codes"]) == new_codes for item in strings)})
    if target_users != list(EXPECTED_TITLE_USERS):
        raise SystemExit(f"unexpected target-label users: {target_users}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(rebuilt)

    before_header = before_blob[:hconfig.PTR_TABLE_OFF + 4 * len(section_pointers(before_blob))]
    after_header = after_blob[:len(before_header)]
    section_table_unchanged = (before_header[hconfig.PTR_TABLE_OFF:]
                               == after_header[hconfig.PTR_TABLE_OFF:])
    if not section_table_unchanged:
        raise SystemExit("a section start moved during append-only relocation")

    return {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_label": SOURCE_LABEL,
        "target_label": new_label,
        "source_codes": list(SOURCE_CODES),
        "target_codes": list(new_codes),
        "source_string_offset": f"0x{source_offset:06X}",
        "new_string_offset": f"0x{trailer:06X}",
        "inline_instruction_offset": f"0x{inline['offset']:06X}",
        "inline_continuation": f"0x{continuation:06X}",
        "retargeted_existing_references": len(references),
        "new_total_references": len(new_references),
        "blob_growth": len(after_blob) - len(before_blob),
        "blob_size_before": len(before_blob),
        "blob_size_after": len(after_blob),
        "blob_sha256_before": sha256(before_blob),
        "blob_sha256_after": sha256(after_blob),
        "blob_checksum_before": hconfig.blob_checksum(before_blob),
        "blob_checksum_after": hconfig.blob_checksum(after_blob),
        "trailer_checksum_before": int.from_bytes(before_blob[-6:-4], "little"),
        "trailer_checksum_after": stored_trailer,
        "trailer_checksum_recomputes": stored_trailer == computed_trailer,
        "section_table_unchanged": section_table_unchanged,
        "existing_payload_offsets_unchanged": True,
        "semantic_rebuild_byte_identical": True,
        "symbolic_users_of_new_string": len(symbolic_users),
        "affected_screens": [
            {"mode": mode, "page": page} for mode, page in target_users
        ],
        "raster_changed_screens": [
            {"mode": mode, "page": page} for mode, page in changed_screens
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_EZHEX)
    parser.add_argument("--out", type=Path, required=True,
                        help="new .EZHex path; must not already exist")
    parser.add_argument("--new-label", default="X96 Boxx",
                        help="longer label using the bounded proof alphabet")
    parser.add_argument("--proof", type=Path,
                        help="optional JSON report path; must not already exist")
    args = parser.parse_args()

    if args.proof and args.proof.exists():
        raise SystemExit(f"refusing to overwrite existing proof: {args.proof}")
    proof = relocate(args.config, args.out, args.new_label)
    if args.proof:
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    print(f"PASS {proof['source_label']!r} -> {proof['target_label']!r}")
    print(f"  appended {proof['blob_growth']} bytes at {proof['new_string_offset']}")
    print(f"  retargeted {proof['retargeted_existing_references']} existing references")
    print(f"  {proof['new_total_references']} symbolic users close on the new string")
    print(f"  semantic users: {len(proof['affected_screens'])}, "
          f"visible raster changes: {len(proof['raster_changed_screens'])}")
    print("  existing payload offsets and section starts unchanged")
    print(args.out.resolve())
    if args.proof:
        print(args.proof.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
