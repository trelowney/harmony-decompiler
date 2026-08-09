"""Make and verify one bounded, length-neutral Harmony 525 label edit.

This is deliberately a proof tool, not a general text editor.  The public 525
sample stores ``X96 Box`` once as a shared font-local glyph string.  Five
device-selection variants and all six X96 pages point at that one string.  This
tool changes it to
the same-length ``X96 BOX``, rebuilds the file through :mod:`hconfig`, and
refuses to write unless the resulting binary differs at exactly the two glyph
bytes expected.

It never opens a remote.  The output path must not already exist, so the source
cannot be overwritten accidentally.
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


MODE_X96 = 111
SOURCE_LABEL = "X96 Box"
SOURCE_CODES = bytes((52, 58, 54, 13, 17, 29, 59))
EXPECTED_TITLE_USERS = (
    (45, 0),
    (82, 0),
    (90, 0),
    (100, 0),
    (101, 0),
    *((MODE_X96, page) for page in range(6)),
)

# These code-to-character assignments are pinned by rendered strings in the
# public sample.  The mapping is font-local, so this intentionally contains
# only the glyphs needed for the bounded proof rather than pretending to be a
# universal character encoding.
TITLE_GLYPHS = {
    " ": 13,
    "6": 54,
    "9": 58,
    "B": 17,
    "O": 3,
    "X": 52,
    "o": 29,
    "x": 59,
}


def encode_title(label: str) -> bytes:
    try:
        return bytes(TITLE_GLYPHS[char] for char in label)
    except KeyError as exc:
        supported = "".join(sorted(TITLE_GLYPHS))
        raise SystemExit(
            f"unsupported title character {exc.args[0]!r}; proof alphabet is {supported!r}"
        ) from None


def find_all(data: bytes, needle: bytes) -> list[int]:
    result = []
    start = 0
    while True:
        at = data.find(needle, start)
        if at < 0:
            return result
        result.append(at)
        start = at + 1


def replace_data_span(doc: dict, offset: int, before: bytes, after: bytes) -> None:
    """Replace one glyph span through its semantic or opaque representation."""
    if len(before) != len(after):
        raise SystemExit("this proof permits length-neutral replacements only")

    semantic = [region for region in doc["blob"]["regions"]
                if region["kind"] == "glyph_string"
                and region["offset"] == offset]
    if semantic:
        if len(semantic) != 1 or bytes(semantic[0]["codes"]) != before:
            raise SystemExit("semantic glyph region does not match the expected source")
        semantic[0]["codes"] = list(after)
        return

    matches = []
    for region in doc["blob"]["regions"]:
        start = region["offset"]
        stop = start + region["length"]
        if start <= offset and offset + len(before) <= stop and "data" in region:
            matches.append(region)
    if len(matches) != 1:
        raise SystemExit(f"glyph span belongs to {len(matches)} writable regions, expected one")

    region = matches[0]
    data = bytearray(hconfig._unhex(region["data"]))
    relative = offset - region["offset"]
    if data[relative:relative + len(before)] != before:
        raise SystemExit("decompiled region does not contain the expected source glyphs")
    data[relative:relative + len(before)] = after
    region["data"] = hconfig._hex_lines(bytes(data))


def rendered_screens(blob: bytes) -> list[tuple[dict, list[list[int]], list[dict]]]:
    sections = semantics.section_offsets(blob)
    font_sets = renderer.fonts(blob, sections)
    result = []
    for entry in renderer.page_roots(blob, sections):
        canvas, strings = renderer.render_page(blob, font_sets, entry["root"])
        result.append((entry, canvas, strings))
    return result


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def edit(source: Path, output: Path, new_label: str) -> dict:
    if source.resolve() == output.resolve():
        raise SystemExit("refusing to overwrite the source config")
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")

    new_codes = encode_title(new_label)
    if len(new_codes) != len(SOURCE_CODES):
        raise SystemExit(
            f"new label encodes to {len(new_codes)} glyphs; expected {len(SOURCE_CODES)}"
        )
    if new_codes == SOURCE_CODES:
        raise SystemExit("new label is identical to the source label")

    raw = source.read_bytes()
    before_blob = hconfig.split_container(raw)[2]
    hits = find_all(before_blob, SOURCE_CODES)
    if len(hits) != 1:
        raise SystemExit(f"source glyph string occurs {len(hits)} times, expected exactly once")
    glyph_offset = hits[0]

    before_pages = rendered_screens(before_blob)
    source_users = sorted({(entry["mode"], entry["page"])
                           for entry, _canvas, strings in before_pages
                           if any(bytes(item["codes"]) == SOURCE_CODES for item in strings)})
    expected_users = list(EXPECTED_TITLE_USERS)
    if source_users != expected_users:
        raise SystemExit(f"unexpected source-label users: {source_users}")

    doc = hconfig.decompile(raw, source.name)
    replace_data_span(doc, glyph_offset, SOURCE_CODES, new_codes)
    rebuilt = hconfig.compile_config(doc)
    after_blob = hconfig.split_container(rebuilt)[2]

    stored_trailer = int.from_bytes(after_blob[-6:-4], "little")
    computed_trailer = hconfig.trailer_checksum(after_blob)
    if stored_trailer != computed_trailer:
        raise SystemExit("edited output has an invalid firmware trailer checksum")

    if len(after_blob) != len(before_blob):
        raise SystemExit("length-neutral edit changed the blob size")
    glyph_diffs = [glyph_offset + index for index, (a, b)
                   in enumerate(zip(SOURCE_CODES, new_codes)) if a != b]
    trailer_diffs = [index for index in range(len(before_blob) - 6, len(before_blob) - 4)
                     if before_blob[index] != after_blob[index]]
    expected_diffs = sorted(glyph_diffs + trailer_diffs)
    actual_diffs = [index for index, (a, b)
                    in enumerate(zip(before_blob, after_blob)) if a != b]
    if actual_diffs != expected_diffs:
        raise SystemExit(
            f"unexpected blob diff offsets: actual={actual_diffs}, expected={expected_diffs}"
        )

    after_pages = rendered_screens(after_blob)
    changed_screens = sorted((before[0]["mode"], before[0]["page"])
                             for before, after in zip(before_pages, after_pages)
                             if before[1] != after[1])
    if changed_screens != expected_users:
        raise SystemExit(f"unexpected rendered screen changes: {changed_screens}")

    target_users = sorted({(entry["mode"], entry["page"])
                           for entry, _canvas, strings in after_pages
                           if any(bytes(item["codes"]) == new_codes for item in strings)})
    if target_users != expected_users:
        raise SystemExit(f"unexpected target-label users: {target_users}")

    identical, roundtripped, _again = hconfig.roundtrip(rebuilt, output.name)
    if not identical or roundtripped != rebuilt:
        raise SystemExit("edited output is not byte-identical after decompile/compile")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(rebuilt)

    before_xml = hconfig.split_container(raw)[0]
    after_xml = hconfig.split_container(rebuilt)[0]
    proof = {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_label": SOURCE_LABEL,
        "target_label": new_label,
        "source_codes": list(SOURCE_CODES),
        "target_codes": list(new_codes),
        "glyph_blob_offset": f"0x{glyph_offset:06X}",
        "glyph_diff_offsets": [f"0x{offset:06X}" for offset in glyph_diffs],
        "blob_diff_offsets": [f"0x{offset:06X}" for offset in actual_diffs],
        "blob_size_before": len(before_blob),
        "blob_size_after": len(after_blob),
        "blob_sha256_before": sha256(before_blob),
        "blob_sha256_after": sha256(after_blob),
        "blob_checksum_before": hconfig.blob_checksum(before_blob),
        "blob_checksum_after": hconfig.blob_checksum(after_blob),
        "trailer_checksum_before": int.from_bytes(before_blob[-6:-4], "little"),
        "trailer_checksum_after": stored_trailer,
        "trailer_checksum_recomputes": stored_trailer == computed_trailer,
        "declared_checksum_before": int(hconfig._tag(before_xml, "CHECKSUM"))
        if before_xml else None,
        "declared_checksum_after": int(hconfig._tag(after_xml, "CHECKSUM"))
        if after_xml else None,
        "affected_screens": [
            {"mode": mode, "page": page} for mode, page in changed_screens
        ],
        "pointer_layout_changed": False,
        "roundtrip_byte_identical": True,
    }
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_EZHEX)
    parser.add_argument("--out", type=Path, required=True,
                        help="new .EZHex path; must not already exist")
    parser.add_argument("--new-label", default="X96 BOX",
                        help="same-length proof label (default: X96 BOX)")
    parser.add_argument("--proof", type=Path,
                        help="optional JSON report path; must not already exist")
    args = parser.parse_args()

    if args.proof and args.proof.exists():
        raise SystemExit(f"refusing to overwrite existing proof: {args.proof}")
    proof = edit(args.config, args.out, args.new_label)
    if args.proof:
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    print(f"PASS {proof['source_label']!r} -> {proof['target_label']!r}")
    print(f"  glyph string: {proof['glyph_blob_offset']}")
    print(f"  blob diffs: {', '.join(proof['blob_diff_offsets'])}")
    print(f"  affected screens: {len(proof['affected_screens'])}")
    print("  pointer layout unchanged; edited output round-trips byte-identically")
    print(args.out.resolve())
    if args.proof:
        print(args.proof.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
