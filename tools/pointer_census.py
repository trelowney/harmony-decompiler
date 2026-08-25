"""Reader-backed census of address fields in an arch-9 Harmony config blob.

The relocation/check split follows Danny Bloemendaal's design in
``harmony-explorations`` commit ``edb1349e669316320341e769c0434bb92c05571a``.
This census is intentionally limited to structures already read by
``tools/hconfig.py``; it does not scan opaque bytes for values that merely look
like addresses.
"""
from __future__ import annotations

from functools import lru_cache

import hconfig


class CensusError(ValueError):
    """The blob is unsupported, or a reader-backed census invariant failed."""


# This is deliberately closed.  verify_arch9_relocation.py asserts both that
# every class is present and that omitting every class makes the check fail.
HOLDER_CLASSES = (
    "blob_header",
    "font_set",
    "ir_body",
    "ir_group",
    "ir_record_header",
    "ir_symbol_table",
    "mode_page",
    "pointer_table",
    "record_header",
    "screen_picture",
    "screen_text",
    "value_map",
)


_REGION_READER = {
    "font_set": "arch9_font_regions",
    "ir_class5_body": "arch9_class5_ir_regions",
    "ir_group": "arch9_class5_ir_regions",
    "ir_record_header": "arch9_class5_ir_regions",
    "ir_symbol_table": "arch9_class5_ir_regions",
    "mode_page": "arch9_mode_page_regions",
    "pointer_table": "parse_pointer_table",
    "record_header": "parse_record_header",
    "raw_pointer": "arch9_value_map_references",
    "screen_picture": "arch9_screen_text_regions",
    "screen_reference": "arch9_screen_text_regions",
}

_REGION_HOLDER = {
    "font_set": "font_set",
    "ir_class5_body": "ir_body",
    "ir_group": "ir_group",
    "ir_record_header": "ir_record_header",
    "ir_symbol_table": "ir_symbol_table",
    "mode_page": "mode_page",
    "pointer_table": "pointer_table",
    "record_header": "record_header",
    "raw_pointer": "value_map",
    "screen_picture": "screen_picture",
    "screen_reference": "screen_text",
}


def _unsupported_name(blob: bytes) -> str:
    """Name an unsupported sample family without asking its reader to try."""
    magic = blob[:4]
    if magic == b"TPTP":
        marker = blob.find(b"DKDK")
        base = int.from_bytes(blob[4:8], "little") - marker if marker >= 0 else None
        if base == 0x30000:
            return "protocol 10 (Harmony 890)"
        return "arch 8 / protocol 8"
    if magic == b"GSPM":
        return "protocol 14 (Harmony 650)"
    return f"unrecognised {magic!r} container"


def require_arch9(blob: bytes) -> None:
    """Refuse unsupported architectures before any arch-9 reader runs."""
    if not isinstance(blob, bytes):
        raise TypeError("census expects an immutable bytes blob")
    if blob[:4] != hconfig.MAGIC:
        raise CensusError(
            "pointer_census supports arch 9 / protocol 9 only; refusing "
            + _unsupported_name(blob))
    if blob[-4:] != hconfig.END_MAGIC:
        raise CensusError("arch 9 / protocol 9 blob does not end with MCHA")


def _section_pointers(blob: bytes, count: int) -> list[int | None]:
    return [
        int.from_bytes(blob[hconfig.PTR_TABLE_OFF + 4 * index:
                            hconfig.PTR_TABLE_OFF + 4 * index + 4], "little") or None
        for index in range(count)
    ]


def _field_specs(region: dict) -> list[tuple[int, int]]:
    """Return ``(field offset, target blob offset)`` from a reader region."""
    kind = region["kind"]
    offset = region["offset"]

    if kind == "pointer_table":
        head = region["count_width"] + len(bytes.fromhex(region.get("header", "")))
        return [(offset + head + 3 * index, target)
                for index, target in enumerate(region["targets"])]
    if kind == "record_header":
        fields = [(offset + 1, region["back_reference"])]
        fields += [(offset + 6 + 3 * index, target)
                   for index, target in enumerate(region["targets"])]
        return fields
    if kind == "ir_group":
        return [(offset + 3 + 3 * index, target)
                for index, target in enumerate(region["targets"])]
    if kind == "ir_record_header":
        fields = [(offset + 8, region["back_reference"])]
        # Zero is a NULL slot, not a stated address, so it is not relocated.
        fields += [(offset + 12 + 3 * index, target)
                   for index, target in enumerate(region["targets"])
                   if target is not None]
        return fields
    if kind == "ir_class5_body":
        return [(offset, region["targets"][0])]
    if kind == "ir_symbol_table":
        return [(offset + 1 + 3 * index, target)
                for index, target in enumerate(region["targets"])]
    if kind == "mode_page":
        return [(offset + 3 * index, target)
                for index, target in enumerate(region["targets"])]
    if kind == "screen_picture":
        return [(offset + 7, region["targets"][0])]
    if kind == "screen_reference":
        return [(offset + 3, region["targets"][0])]
    if kind == "font_set":
        # hconfig preserves significant NULL glyph slots as None.  They are
        # pointer-shaped fields, but do not state an address and must stay zero.
        return [(offset + 3 + 3 * index, target)
                for index, target in enumerate(region["targets"])
                if target is not None]
    if kind == "raw_pointer":
        return [(offset, region["targets"][0])]
    raise CensusError(f"no field layout for reader region {kind!r}")


def _same_reader_region(actual: dict, expected: dict) -> bool:
    keys = ("kind", "offset", "length", "targets", "back_reference",
            "count_width", "header")
    return all(actual.get(key) == expected.get(key) for key in keys)


def _reader_regions(blob: bytes, section_pointers: list[int | None]) -> dict:
    screen = hconfig.arch9_screen_text_regions(blob, section_pointers)
    direct = [
        *hconfig.arch9_class5_ir_regions(blob, section_pointers),
        *hconfig.arch9_font_regions(blob, section_pointers),
        *hconfig.arch9_mode_page_regions(blob, section_pointers),
        *hconfig.arch9_value_map_references(blob, section_pointers),
        *screen,
    ]
    return {(region["kind"], region["offset"]): region for region in direct}


def _assert_screen_flow_has_no_unrepresented_pointers(
        blob: bytes, section_pointers: list[int | None]) -> None:
    """Fail closed if a future sample populates an untested screen-flow class."""
    pending = list(hconfig.arch9_screen_roots(blob, section_pointers))
    seen: set[int] = set()
    while pending:
        root = pending.pop()
        if root in seen:
            continue
        seen.add(root)
        program = hconfig.parse_arch9_screen_program(blob, root)
        if program is None:
            continue
        for instruction in program:
            if instruction["targets"]:
                raise CensusError(
                    "parse_arch9_screen_program found control-flow address fields; "
                    "the public sample has no negative-test-backed holder class for them")


def _record_ranges(blob: bytes, section_pointers: list[int | None]):
    if len(section_pointers) <= 6 or section_pointers[6] is None:
        raise CensusError("arch 9 section 6 is absent")
    starts = hconfig.find_record_starts(
        blob, section_pointers[6] - hconfig.CONFIG_BASE)
    if not starts:
        raise CensusError("find_record_starts found no section-6 records")
    first_section = min(pointer for pointer in section_pointers if pointer is not None)
    first_section -= hconfig.CONFIG_BASE
    for index, start in enumerate(starts):
        yield start, starts[index + 1] if index + 1 < len(starts) else first_section


def _assert_superseded_readers_are_covered(
        blob: bytes, section_pointers: list[int | None], by_at: dict[int, dict],
        glyph_strings: list[dict]) -> None:
    """Prove weaker/overlapping hconfig readers add no address field here."""
    glyph_spans = [(region["offset"], region["offset"] + region["length"])
                   for region in glyph_strings]

    for start, limit in _record_ranges(blob, section_pointers):
        header = hconfig.parse_record_header(blob, start, limit)
        if header is None:
            raise CensusError(f"parse_record_header refused record 0x{start:X}")
        trailer = hconfig.parse_record_trailer(blob, start + header["length"], limit)
        body_end = limit
        if trailer is not None:
            body_end = trailer["offset"]
            for index, target in enumerate(trailer["targets"]):
                at = trailer["offset"] + 1 + 3 * index
                entry = by_at.get(at)
                if (entry is None or entry["holder"] != "mode_page"
                        or entry["target"] != target + hconfig.CONFIG_BASE):
                    raise CensusError(
                        f"parse_record_trailer field at 0x{at:X} is not exactly "
                        "the stronger mode_page field")

        # _split_references uses these same two readers.  The rooted screen walk
        # supersedes their byte-pattern interpretation on the public arch-9
        # shape; any genuinely new field is a census change and must fail here.
        for opcode_at, target in hconfig.find_references(
                blob, start + header["length"], body_end):
            at = opcode_at + 1
            entry = by_at.get(at)
            if entry is not None and entry["target"] == target + hconfig.CONFIG_BASE:
                continue
            if any(left <= at and at + 3 <= right for left, right in glyph_spans):
                continue
            raise CensusError(
                f"find_references found an unrepresented field at 0x{at:X}")

        cursor = start + header["length"]
        while cursor < body_end:
            block = hconfig.parse_block_header(blob, cursor, body_end)
            if block is not None:
                at = cursor + 9
                entry = by_at.get(at)
                target = block["targets"][0] + hconfig.CONFIG_BASE
                if (entry is None or entry["holder"] != "screen_picture"
                        or entry["target"] != target):
                    raise CensusError(
                        f"parse_block_header field at 0x{at:X} is not exactly "
                        "the stronger screen_picture field")
                cursor += block["length"]
            else:
                cursor += 1


@lru_cache(maxsize=8)
def _census_cached(blob: bytes) -> tuple[tuple[tuple[str, object], ...], ...]:
    require_arch9(blob)
    try:
        doc = hconfig.decompile(blob)
    except Exception as exc:
        raise CensusError(f"hconfig.decompile refused the blob: {exc}") from exc

    regions = doc["blob"]["regions"]
    header = next((region for region in regions
                   if region["kind"] == "blob_header"), None)
    if header is None or header.get("architecture") != "arch 9":
        raise CensusError("pointer_census supports arch 9 / protocol 9 only")
    if header["section_count"] != 18:
        raise CensusError(
            f"arch 9 reader exposed {header['section_count']} section slots, expected 18")

    base = hconfig.CONFIG_BASE
    expected_end = base + len(blob) - 4
    stated_end = int.from_bytes(blob[4:8], "little")
    if stated_end != expected_end:
        raise CensusError(
            f"end_addr states 0x{stated_end:X}, expected 0x{expected_end:X}")

    section_pointers = _section_pointers(blob, header["section_count"])
    direct = _reader_regions(blob, section_pointers)
    _assert_screen_flow_has_no_unrepresented_pointers(blob, section_pointers)

    entries: list[dict] = []
    by_at: dict[int, dict] = {}

    def add(at: int, width: int, target: int, holder: str, reader: str) -> None:
        if holder not in HOLDER_CLASSES:
            raise CensusError(f"unreviewed holder class {holder!r}")
        if not 0 <= at <= len(blob) - width:
            raise CensusError(f"{reader} exposed field outside blob at 0x{at:X}")
        written = int.from_bytes(blob[at:at + width], "little")
        if written != target:
            raise CensusError(
                f"{reader} says field 0x{at:X} targets 0x{target:X}, "
                f"but its bytes state 0x{written:X}")
        lands = target - base
        lands = lands if 0 <= lands < len(blob) else None
        entry = {
            "at": at,
            "width": width,
            "target": target,
            "lands": lands,
            "holder": holder,
            "reader": reader,
        }
        previous = by_at.get(at)
        if previous is not None:
            if previous != entry:
                raise CensusError(
                    f"readers disagree about address field at 0x{at:X}")
            return
        by_at[at] = entry
        entries.append(entry)

    # decompile() reads end_addr and the 18 section addresses.  end_addr is
    # also restamped by relocate_arch9 after ordinary pointer rewriting.
    add(4, 4, stated_end, "blob_header", "decompile")
    for index, target in enumerate(section_pointers):
        if target is not None:
            add(hconfig.PTR_TABLE_OFF + 4 * index, 4, target,
                "blob_header", "decompile")

    supported_region_kinds = set(_REGION_READER)
    address_region_kinds = {
        "block_header", "font_set", "ir_class5_body", "ir_group",
        "ir_record_header", "ir_symbol_table", "mode_page", "pointer_table",
        "raw_pointer", "record_header", "record_trailer", "reference",
        "screen_picture", "screen_reference",
    }
    unexpected = sorted({region["kind"] for region in regions}
                        & (address_region_kinds - supported_region_kinds))
    if unexpected:
        raise CensusError(
            "final hconfig region tiling contains unreviewed address classes: "
            + ", ".join(unexpected))

    for region in regions:
        kind = region["kind"]
        if kind not in supported_region_kinds:
            continue
        if kind == "pointer_table":
            parsed = hconfig.parse_pointer_table(
                blob, region["offset"], region["offset"] + region["length"], len(blob))
            if parsed is None or not _same_reader_region(region, parsed):
                raise CensusError(
                    f"parse_pointer_table no longer reproduces region 0x{region['offset']:X}")
        elif kind == "record_header":
            parsed = hconfig.parse_record_header(
                blob, region["offset"], region["offset"] + region["length"])
            if parsed is None or not _same_reader_region(region, parsed):
                raise CensusError(
                    f"parse_record_header no longer reproduces region 0x{region['offset']:X}")
        else:
            parsed = direct.get((kind, region["offset"]))
            if parsed is None or not _same_reader_region(region, parsed):
                raise CensusError(
                    f"{_REGION_READER[kind]} no longer reproduces "
                    f"{kind} region 0x{region['offset']:X}")

        holder = _REGION_HOLDER[kind]
        reader = _REGION_READER[kind]
        for at, target_offset in _field_specs(region):
            add(at, 3, target_offset + base, holder, reader)

    glyph_strings = [region for region in direct.values()
                     if region["kind"] == "glyph_string"]
    _assert_superseded_readers_are_covered(
        blob, section_pointers, by_at, glyph_strings)

    entries.sort(key=lambda entry: entry["at"])
    for left, right in zip(entries, entries[1:]):
        if left["at"] + left["width"] > right["at"]:
            raise CensusError(
                f"address fields overlap at 0x{left['at']:X} and 0x{right['at']:X}")

    frozen = tuple(tuple(entry.items()) for entry in entries)
    return frozen


def census(blob: bytes) -> list[dict]:
    """Return one dictionary per reader-backed field that states an address."""
    return [dict(items) for items in _census_cached(blob)]


def holder_classes(entries: list[dict]) -> tuple[str, ...]:
    """The sorted holder classes actually present in a census."""
    return tuple(sorted({entry["holder"] for entry in entries}))


__all__ = [
    "CensusError", "HOLDER_CLASSES", "census", "holder_classes", "require_arch9",
]
