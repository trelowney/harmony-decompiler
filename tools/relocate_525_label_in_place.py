"""Make a longer 525 label without growing the container or moving the picture bank.

This bounded experiment grows the one inline ``X96 Box`` instruction by one
byte into the inline string that immediately follows it. That following string
is already the exact suffix of another live string, so it is converted to an
external draw and its other users are retargeted to those byte-identical suffix
bytes. A jump preserves the original continuation. No section, picture, action,
IR record or existing payload moves.

It is an offline proof, not a hardware-safe editor. The source and every output
must be distinct, and existing outputs are never overwritten.
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
from relocate_525_label import EXPECTED_RASTER_CHANGES


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def section_pointers(blob: bytes) -> list[int | None]:
    marker = blob.index(hconfig.HEADER_MARKER)
    pointers = [int.from_bytes(blob[offset:offset + 4], "little") or None
                for offset in range(hconfig.PTR_TABLE_OFF, marker, 4)]
    while pointers and pointers[-1] is None:
        pointers.pop()
    return pointers


def text_regions(blob: bytes) -> list[dict]:
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
        raise SystemExit(f"found {len(unique)} inline source labels, expected one")
    return next(iter(unique.values()))


def wrap_container(source: bytes, blob: bytes) -> bytes:
    xml, separator, _ = hconfig.split_container(source)
    if xml is None:
        return blob
    xml = hconfig._set_tag(xml, "BINARYDATASIZE", len(blob))
    xml = hconfig._set_tag(xml, "CHECKSUM", hconfig.blob_checksum(blob))
    return xml + separator + blob


def pointer(offset: int) -> bytes:
    return (offset + hconfig.CONFIG_BASE).to_bytes(3, "little")


def find_adjacent_suffix_donor(
        regions: list[dict], blob: bytes, inline: dict) -> tuple[dict, dict, int, dict]:
    """Find the next inline string and a distinct live string with the same suffix."""
    strings = [region for region in regions if region["kind"] == "glyph_string"]
    inline_end = inline["offset"] + 1 + len(inline["operands"])
    donor_inline = semantics.screen_program(blob, inline_end)
    if not donor_inline or donor_inline[0]["offset"] != inline_end:
        raise SystemExit("the source inline instruction has no following program")
    donor_instruction = donor_inline[0]
    if donor_instruction["opcode"] != 5:
        raise SystemExit("the instruction after X96 is not an inline string")
    donor_codes = bytes(donor_instruction["operands"][2:-1])
    donor_offset = donor_instruction["offset"] + 3
    donor = next((item for item in strings if item["offset"] == donor_offset), None)
    if donor is None or bytes(donor["codes"]) != donor_codes:
        raise SystemExit("the adjacent inline string is not an external glyph region")

    hosts = []
    for host in strings:
        host_bytes = bytes(host["codes"])
        if host is donor or len(host_bytes) <= len(donor_codes):
            continue
        if host_bytes.endswith(donor_codes):
            hosts.append((host, len(host_bytes) - len(donor_codes)))
    if not hosts:
        raise SystemExit("the adjacent string has no byte-identical live suffix")
    host, delta = min(hosts, key=lambda item: item[0]["offset"])
    return donor, host, delta, donor_instruction


def changed_offsets(before: bytes, after: bytes) -> set[int]:
    if len(before) != len(after):
        raise SystemExit("length-neutral candidate changed size")
    return {index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}


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
    regions = text_regions(before_blob)
    source_offset = before_blob.find(SOURCE_CODES)
    if source_offset < 0 or before_blob.find(SOURCE_CODES, source_offset + 1) >= 0:
        raise SystemExit("source glyph string is not unique")

    source_users = [region for region in regions
                    if region["kind"] == "screen_reference"
                    and region["targets"] == [source_offset]]
    if len(source_users) != 24:
        raise SystemExit(f"found {len(source_users)} external X96 users, expected 24")

    inline = source_inline_instruction(before_blob)
    inline_length = 1 + len(inline["operands"])
    continuation = inline["offset"] + inline_length
    if inline_length != 11:
        raise SystemExit(f"source inline instruction is {inline_length} bytes, expected 11")

    donor, host, suffix_delta, donor_inline = find_adjacent_suffix_donor(
        regions, before_blob, inline)
    donor_offset = donor["offset"]
    host_suffix = host["offset"] + suffix_delta
    donor_users = [region for region in regions
                   if region["kind"] == "screen_reference"
                   and region["targets"] == [donor_offset]]
    if not donor_users:
        raise SystemExit("the reusable region has no live users")
    if before_blob[host_suffix:host_suffix + donor["length"]] != bytes(donor["codes"]) + b"\x00":
        raise SystemExit("host suffix is not byte-identical to the donor string")

    patched = bytearray(before_blob)
    for reference in donor_users:
        patched[reference["offset"] + 3:reference["offset"] + 6] = pointer(host_suffix)

    donor_end = donor_inline["offset"] + 1 + len(donor_inline["operands"])
    x, y = inline["operands"][:2]
    donor_x, donor_y = donor_inline["operands"][:2]
    replacement = (bytes((5, x, y)) + new_codes + b"\x00"
                   + bytes((4, donor_x, donor_y)) + pointer(host_suffix)
                   + bytes((20,)) + pointer(donor_end))
    replacement += bytes(donor_end - inline["offset"] - len(replacement))
    if len(replacement) != donor_end - inline["offset"]:
        raise SystemExit("combined inline replacement does not preserve its extent")
    patched[inline["offset"]:donor_end] = replacement

    checksum_at = len(patched) - hconfig.TRAILER_CHECKSUM_OFFSET
    patched[checksum_at:checksum_at + 2] = hconfig.trailer_checksum(patched).to_bytes(2, "little")
    after_blob = bytes(patched)
    candidate = wrap_container(raw, after_blob)

    doc = hconfig.symbolise(hconfig.decompile(candidate, output.name))
    rebuilt = hconfig.compile_config(doc)
    if rebuilt != candidate:
        difference = hconfig.first_difference(candidate, rebuilt)
        raise SystemExit(f"symbolic rebuild differs at file offset 0x{difference:X}")

    stored_trailer = int.from_bytes(after_blob[-6:-4], "little")
    computed_trailer = hconfig.trailer_checksum(after_blob)
    if stored_trailer != computed_trailer:
        raise SystemExit("candidate has an invalid firmware trailer checksum")
    if len(after_blob) != len(before_blob):
        raise SystemExit("candidate grew despite in-place relocation")
    if before_blob[4:8] != after_blob[4:8]:
        raise SystemExit("container end address changed")
    before_sections = before_blob[hconfig.PTR_TABLE_OFF:before_blob.index(hconfig.HEADER_MARKER)]
    after_sections = after_blob[hconfig.PTR_TABLE_OFF:after_blob.index(hconfig.HEADER_MARKER)]
    if before_sections != after_sections:
        raise SystemExit("section pointer table changed")

    after_regions = text_regions(after_blob)
    target_refs = [region for region in after_regions
                   if region["kind"] == "screen_reference"
                   and region["targets"] == [source_offset]]
    suffix_refs = [region for region in after_regions
                   if region["kind"] == "screen_reference"
                   and region["targets"] == [host_suffix]]
    if len(target_refs) != 24 or len(suffix_refs) != len(donor_users) + 1:
        raise SystemExit(
            f"reference closure failed: target={len(target_refs)}, suffix={len(suffix_refs)}")

    symbolic_target = next(region for region in doc["blob"]["regions"]
                           if region["kind"] == "glyph_string"
                           and region["codes"] == list(new_codes))
    symbolic_target_users = [region for region in doc["blob"]["regions"]
                             if region["kind"] == "screen_reference"
                             and region["targets"] == [{"to": symbolic_target["id"]}]]
    if len(symbolic_target_users) != 24:
        raise SystemExit(f"only {len(symbolic_target_users)} symbolic X96 users close")

    symbolic_host = next(region for region in doc["blob"]["regions"]
                         if region["kind"] == "glyph_string"
                         and region["offset"] == host["offset"])
    symbolic_suffix = {"to": symbolic_host["id"], "delta": suffix_delta}
    symbolic_suffix_users = [region for region in doc["blob"]["regions"]
                             if region["kind"] == "screen_reference"
                             and region["targets"] == [symbolic_suffix]]
    if len(symbolic_suffix_users) != len(donor_users) + 1:
        raise SystemExit("suffix users did not become region-plus-delta pointers")

    before_pages = rendered_screens(before_blob)
    after_pages = rendered_screens(after_blob)
    if [(item[0]["mode"], item[0]["page"]) for item in before_pages] != [
            (item[0]["mode"], item[0]["page"]) for item in after_pages]:
        raise SystemExit("screen population changed")
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

    allowed = set(range(inline["offset"], donor_end))
    allowed |= set(range(checksum_at, checksum_at + 2))
    for reference in donor_users:
        allowed |= set(range(reference["offset"] + 3, reference["offset"] + 6))
    actual = changed_offsets(before_blob, after_blob)
    unexpected = sorted(actual - allowed)
    if unexpected:
        raise SystemExit(f"unexpected changed offsets: {unexpected}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(candidate)
    return {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_label": SOURCE_LABEL,
        "target_label": new_label,
        "source_codes": list(SOURCE_CODES),
        "target_codes": list(new_codes),
        "source_string_offset": f"0x{source_offset:06X}",
        "adjacent_string_offset": f"0x{donor_offset:06X}",
        "adjacent_string_old_codes": donor["codes"],
        "suffix_host_offset": f"0x{host['offset']:06X}",
        "suffix_delta": suffix_delta,
        "suffix_target_offset": f"0x{host_suffix:06X}",
        "retargeted_reused_region_users": len(donor_users),
        "unchanged_external_x96_references": len(source_users),
        "new_total_external_x96_references": len(target_refs),
        "inline_instruction_offset": f"0x{inline['offset']:06X}",
        "adjacent_inline_offset": f"0x{donor_inline['offset']:06X}",
        "combined_continuation": f"0x{donor_end:06X}",
        "blob_growth": 0,
        "blob_size": len(after_blob),
        "blob_sha256_before": sha256(before_blob),
        "blob_sha256_after": sha256(after_blob),
        "trailer_checksum_before": int.from_bytes(before_blob[-6:-4], "little"),
        "trailer_checksum_after": stored_trailer,
        "section_table_unchanged": True,
        "container_end_unchanged": True,
        "symbolic_rebuild_byte_identical": True,
        "changed_blob_offsets": [f"0x{offset:06X}" for offset in sorted(actual)],
        "affected_screens": [{"mode": mode, "page": page} for mode, page in target_users],
        "raster_changed_screens": [
            {"mode": mode, "page": page} for mode, page in changed_screens
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_EZHEX)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--new-label", default="X96 Boxx")
    parser.add_argument("--proof", type=Path)
    args = parser.parse_args()
    if args.proof and args.proof.exists():
        raise SystemExit(f"refusing to overwrite existing proof: {args.proof}")
    result = relocate(args.config, args.out, args.new_label)
    if args.proof:
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"PASS {result['source_label']!r} -> {result['target_label']!r}")
    print(f"  expanded inline string through {result['adjacent_string_offset']}")
    print(f"  preserved {result['retargeted_reused_region_users']} old users by suffix alias")
    print(f"  preserved {result['unchanged_external_x96_references']} external X96 pointers")
    print("  container size, end address, section table and picture-bank position unchanged")
    print(args.out.resolve())
    if args.proof:
        print(args.proof.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
