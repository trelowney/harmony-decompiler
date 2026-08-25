"""Positive, refusal, and deliberately negative checks for arch-9 relocation.

The negative suite is the deliverable: each exact holder class is omitted in
turn, and the identical mechanical-plus-meaning check that passes whole must
then fail.  This follows Danny Bloemendaal's relocation-test design in
``harmony-explorations`` commit ``edb1349e669316320341e769c0434bb92c05571a``.

This check cannot establish that unchanged or length-neutral semantics are
correct.  For example, changing a key-table u16 to a different valid action-list
index and restamping the checksums can preserve every pointer, region count,
device count, and round trip checked here while making a button do the wrong
thing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path

import _paths
import count_devices
import hconfig
from pointer_census import (
    CensusError,
    HOLDER_CLASSES,
    census,
    holder_classes,
)
from relocate_arch9 import RelocateError, relocate, relocation_floor


SAMPLE_DIR = Path(hconfig.__file__).resolve().parents[1] / "samples" / "harmony525"
PUBLIC_BIN = SAMPLE_DIR / "config.bin"
PUBLIC_EZHEX = SAMPLE_DIR / "config.EZHex"
DELTA = 3
DERIVED_DELTA = 5
ENTRY_KEYS = ("at", "width", "target", "lands", "holder", "reader")


@dataclass(frozen=True)
class Baseline:
    kinds: Counter
    devices: int | None


@dataclass(frozen=True)
class CaseResult:
    mechanical: bool
    inventory: bool
    devices: bool
    roundtrip: bool
    details: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.mechanical and self.inventory and self.devices and self.roundtrip


def _baseline(blob: bytes) -> Baseline:
    doc = hconfig.decompile(blob)
    rebuilt = hconfig.compile_config(doc)
    if rebuilt != blob:
        difference = hconfig.first_difference(blob, rebuilt)
        raise AssertionError(f"input round trip differs first at 0x{difference:X}")
    return Baseline(
        Counter(region["kind"] for region in doc["blob"]["regions"]),
        count_devices.devices_from_section_5(blob),
    )


def insertion_offsets(blob: bytes) -> tuple[int, int]:
    """Two checked boundaries: content floor and one late addressed target.

    The late target is the first IR symbol block immediately after a still-raw
    section region.  It is reader-addressed, and insertion there extends the
    raw predecessor without splitting the independently addressed structure.
    """
    floor = relocation_floor(blob)
    regions = hconfig.decompile(blob)["blob"]["regions"]
    addressed_start = next((right["offset"] for left, right
                              in zip(regions, regions[1:])
                              if left["kind"] == "section"
                              and right["kind"] == "ir_symbol_block"), None)
    if addressed_start is None:
        raise AssertionError("no raw-to-ir_symbol_block insertion boundary was found")
    if addressed_start not in {entry["lands"] for entry in census(blob)}:
        raise AssertionError("IR-symbol insertion boundary is not reader-addressed")
    offsets = (floor, addressed_start)
    trailer = len(blob) - hconfig.TRAILER_CHECKSUM_OFFSET
    if len(set(offsets)) != 2 or not all(floor <= at <= trailer for at in offsets):
        raise AssertionError(f"bad insertion offset set {offsets!r}")
    return offsets


def holder_offset_coverage(
        cases: list[tuple[str, bytes, Baseline, int]]) -> dict[str, tuple[int, int]]:
    """Assert and count which tested offsets actually exercise each class.

    A class is exercised at an offset when at least one of its stated pointers
    lands at or above the insertion, which is the same predicate relocation
    uses to decide whether that field must move.
    """
    coverage: dict[str, tuple[int, int]] = {}
    for holder in HOLDER_CLASSES:
        offsets_exercised = 0
        pointer_occurrences = 0
        for _name, blob, _baseline, at in cases:
            above = sum(
                entry["holder"] == holder
                and entry["lands"] is not None
                and entry["lands"] >= at
                for entry in census(blob)
            )
            if above:
                offsets_exercised += 1
                pointer_occurrences += above
        if offsets_exercised == 0:
            raise AssertionError(
                f"holder class {holder!r} is not exercised by any tested offset")
        coverage[holder] = (offsets_exercised, pointer_occurrences)
    return coverage


def _naive_shift(blob: bytes, at: int, delta: int) -> bytes:
    return blob[:at] + bytes(delta) + blob[at:]


def _mechanical_expected(blob: bytes, at: int, delta: int) -> tuple[bytes, set[int]]:
    naive = _naive_shift(blob, at, delta)
    expected = bytearray(naive)
    entries = census(blob)
    rewritten_fields: list[range] = []
    for entry in entries:
        if entry["lands"] is None or entry["lands"] < at:
            continue
        field_at = entry["at"] + (delta if entry["at"] >= at else 0)
        expected[field_at:field_at + entry["width"]] = (
            entry["target"] + delta).to_bytes(entry["width"], "little")
        rewritten_fields.append(range(field_at, field_at + entry["width"]))

    expected[4:8] = (
        int.from_bytes(blob[4:8], "little") + delta).to_bytes(4, "little")
    checksum_at = len(expected) - hconfig.TRAILER_CHECKSUM_OFFSET
    expected[checksum_at:checksum_at + 2] = (
        hconfig.trailer_checksum(expected).to_bytes(2, "little"))

    actual_diff = {index for index, (left, right) in enumerate(zip(naive, expected))
                   if left != right}
    allowed = set(range(4, 8)) | set(range(checksum_at, checksum_at + 2))
    for field in rewritten_fields:
        allowed.update(field)
        if not any(index in actual_diff for index in field):
            raise AssertionError(
                f"rewritten field at 0x{field.start:X} caused no byte difference")
    if not actual_diff <= allowed:
        outside = min(actual_diff - allowed)
        raise AssertionError(f"mechanical oracle changed unowned byte 0x{outside:X}")
    return bytes(expected), actual_diff


def verify_case(
        blob: bytes, baseline: Baseline, at: int, delta: int,
        omit: str | None = None) -> CaseResult:
    """Run both halves; expected bytes always use the complete census."""
    details: list[str] = []
    expected, expected_diff = _mechanical_expected(blob, at, delta)
    relocated = relocate(blob, at, delta, omit=omit)
    naive = _naive_shift(blob, at, delta)
    actual_diff = {index for index, (left, right) in enumerate(zip(naive, relocated))
                   if left != right}
    mechanical = relocated == expected and actual_diff == expected_diff
    if not mechanical:
        details.append(
            f"mechanical diff expected {len(expected_diff)} changed byte(s), "
            f"got {len(actual_diff)}")

    inventory_ok = devices_ok = roundtrip_ok = False
    try:
        doc = hconfig.decompile(relocated)
        kinds = Counter(region["kind"] for region in doc["blob"]["regions"])
        inventory_ok = kinds == baseline.kinds
        if not inventory_ok:
            details.append(f"region-kind inventory changed: {kinds - baseline.kinds}; "
                           f"missing {baseline.kinds - kinds}")
        device_count = count_devices.devices_from_section_5(relocated)
        devices_ok = device_count == baseline.devices
        if not devices_ok:
            details.append(
                f"device count changed from {baseline.devices} to {device_count}")
        rebuilt = hconfig.compile_config(doc)
        roundtrip_ok = rebuilt == relocated
        if not roundtrip_ok:
            difference = hconfig.first_difference(relocated, rebuilt)
            details.append(f"round trip first differs at 0x{difference:X}")
    except Exception as exc:
        details.append(f"meaning reader refused: {type(exc).__name__}: {exc}")

    return CaseResult(mechanical, inventory_ok, devices_ok, roundtrip_ok,
                      tuple(details))


def _expect_refusal(blob: bytes, at: int, delta: int, contains: str) -> None:
    try:
        relocate(blob, at, delta)
    except RelocateError as exc:
        if contains not in str(exc):
            raise AssertionError(
                f"refusal {exc!s} did not name expected reason {contains!r}") from exc
        return
    raise AssertionError(f"relocation at {at} by {delta} should have been refused")


def refusal_checks(blob: bytes) -> None:
    floor = relocation_floor(blob)
    trailer = len(blob) - hconfig.TRAILER_CHECKSUM_OFFSET
    entries = census(blob)

    _expect_refusal(blob, floor - 1, 1, "outside content")
    _expect_refusal(blob, trailer + 1, 1, "outside content")

    splittable = next(entry for entry in entries
                      if entry["width"] == 3 and floor <= entry["at"] < trailer - 2)
    _expect_refusal(blob, splittable["at"] + 1, 1, "splits")

    movable_u24 = [entry for entry in entries
                   if entry["width"] == 3 and entry["lands"] is not None
                   and entry["lands"] >= floor]
    highest = max(movable_u24, key=lambda entry: entry["target"])
    overflow_delta = 0x1000000 - highest["target"]
    _expect_refusal(blob, floor, overflow_delta, "past a u24")


def structure_refusals(blob: bytes) -> list[str]:
    """Offsets that break no pointer and still cost the decompiler structure.

    The census is what relocation rewrites, so it cannot answer whether the gap
    landed somewhere a config can hold one - a gap in the wrong place leaves
    every stated address correct. Both cases below are found in the file rather
    than written down as numbers, and both were allowed before the guard.
    """
    floor = relocation_floor(blob)
    regions = hconfig.decompile(blob)["blob"]["regions"]
    reason = "costs the decompiler structure"
    found = []

    # One byte into a glyph. The insertion splits an object rather than a field,
    # so no census entry is disturbed and the pixels stop being a glyph.
    glyph = next((region for region in regions
                  if region.get("kind") == "font_glyph"
                  and region.get("offset", 0) > floor
                  and hconfig.region_length(region) > 2), None)
    if glyph is not None:
        _expect_refusal(blob, glyph["offset"] + 1, 1, reason)
        found.append(f"one byte into the font_glyph at {glyph['offset']}")

    # The start of the section after the one holding the tagged lists. Section
    # 8's last list ends exactly on that boundary (FORMAT.md 4k), so a gap there
    # pads a span whose reader walks it end to end and the walk stops early.
    walked = {region.get("section") for region in regions
              if region.get("kind") == "tagged_list"}
    walked.discard(None)
    for section in sorted(walked):
        after = hconfig.CONFIG_BASE
        start = int.from_bytes(blob[0x0C + 4 * (section + 1):
                                    0x10 + 4 * (section + 1)], "little") - after
        if start > floor:
            _expect_refusal(blob, start, 32, reason)
            found.append(f"the start of section {section + 1} at {start}, "
                         f"which is where section {section} ends")
    return found


def _unique_public_blob() -> bytes:
    if not PUBLIC_BIN.is_file() or not PUBLIC_EZHEX.is_file():
        raise AssertionError("the public Harmony 525 sample pair is incomplete")
    bare = PUBLIC_BIN.read_bytes()
    wrapped = _paths.get_blob(PUBLIC_EZHEX)
    if bare != wrapped:
        raise AssertionError("config.bin and config.EZHex contain different arch-9 blobs")
    return bare


def _named_unsupported_refusals() -> list[str]:
    """Require census and relocation to name every unsupported sample family."""
    paths = (
        (next(iter(sorted(_paths.ARCH8_DIR.glob("*.EZHex"))), None),
         "arch 8 / protocol 8"),
        (_paths.REPO / "samples" / "harmony890" / "H890-Bedroom-1.EZHex",
         "protocol 10"),
        (_paths.REPO / "samples" / "harmony650" / "Harmony_650.EZHex",
         "protocol 14"),
    )
    observed: list[str] = []
    for path, family in paths:
        if path is None or not path.is_file():
            raise AssertionError(f"missing unsupported-family sample for {family}")
        blob = _paths.get_blob(path)
        for operation, call in (
                ("census", lambda: census(blob)),
                ("relocate", lambda: relocate(blob, 0, 1))):
            try:
                call()
            except (CensusError, RelocateError) as exc:
                if family not in str(exc):
                    raise AssertionError(
                        f"{operation} refusal did not name {family}: {exc}") from exc
            else:
                raise AssertionError(f"{operation} accepted unsupported {family}")
        observed.append(family)
    return observed


def main() -> int:
    public = _unique_public_blob()
    public_entries = census(public)
    if any(tuple(entry) != ENTRY_KEYS for entry in public_entries):
        raise AssertionError("census entry schema changed")
    if len({entry["at"] for entry in public_entries}) != len(public_entries):
        raise AssertionError("census contains duplicate field offsets")
    observed = holder_classes(public_entries)
    if observed != HOLDER_CLASSES:
        raise AssertionError(
            f"holder class list changed: expected {HOLDER_CLASSES}, got {observed}")

    floor = relocation_floor(public)
    if floor != 0x5F:
        raise AssertionError(f"public arch-9 floor moved from 0x5F to 0x{floor:X}")
    if insertion_offsets(public)[1] != 0xF528:
        raise AssertionError("public late insertion boundary moved from 0xF528")
    public_base = _baseline(public)
    refusal_checks(public)
    structural = structure_refusals(public)
    unsupported = _named_unsupported_refusals()

    # A second case is useful for re-census, but it is derived from the one
    # public sample and therefore is not independent evidence.
    derive_at = insertion_offsets(public)[1]
    derived = relocate(public, derive_at, DERIVED_DELTA)
    derived_base = _baseline(derived)
    if derived_base != public_base:
        raise AssertionError("derived arch-9 input changed the baseline inventory")
    if holder_classes(census(derived)) != HOLDER_CLASSES:
        raise AssertionError("derived input changed the exact holder class list")
    refusal_checks(derived)

    samples = (("public", public, public_base),
               ("derived", derived, derived_base))
    cases: list[tuple[str, bytes, Baseline, int]] = []
    for name, blob, baseline in samples:
        for at in insertion_offsets(blob):
            cases.append((name, blob, baseline, at))

    coverage = holder_offset_coverage(cases)

    for name, blob, baseline, at in cases:
        result = verify_case(blob, baseline, at, DELTA)
        if not result.passed:
            raise AssertionError(
                f"positive {name} case at 0x{at:X} failed: "
                + "; ".join(result.details))

    print("positive matrix: PASS")
    print(f"  one independent public blob: {len(public)} bytes, "
          f"sha256 {hashlib.sha256(public).hexdigest()}")
    print("  one derived (not independent) input: "
          f"+{DERIVED_DELTA} bytes at 0x{derive_at:X}")
    print(f"  {len(cases)} cases: delta {DELTA}, offsets "
          + ", ".join(f"{name}=0x{at:X}" for name, _, _, at in cases))
    print(f"  census: {len(public_entries)} stated address fields")
    counts = Counter(entry["holder"] for entry in public_entries)
    for holder in HOLDER_CLASSES:
        print(f"    {holder}: {counts[holder]}")
    print("arch-9 floor: PASS")
    print("  0x5F, the byte after CMAH; section 6's operand * 3 + 3 u24 table")
    print("  states record placement, while the header/table/marker below it are fixed")
    print("refusals: PASS (below floor, inside trailer, split field, u24 overflow)")
    print("unsupported families: PASS (named refusal: " + ", ".join(unsupported) + ")")

    print("negative-matrix holder coverage:")
    for holder in HOLDER_CLASSES:
        offsets_exercised, pointer_occurrences = coverage[holder]
        print(
            f"  {holder}: {offsets_exercised}/{len(cases)} tested offsets; "
            f"{pointer_occurrences} pointer occurrence(s) land at or above them"
        )

    # The complete census remains the oracle while relocate omits one class.
    # Running the same cases and both halves makes a false negative visible.
    print("negative matrix (each holder omitted):")
    for holder in HOLDER_CLASSES:
        results = [verify_case(blob, baseline, at, DELTA, omit=holder)
                   for _, blob, baseline, at in cases]
        failed = [result for result in results if not result.passed]
        if not failed:
            raise AssertionError(
                f"NEGATIVE TEST FAILED TO FAIL with holder {holder!r} omitted")
        mechanical_failures = sum(not result.mechanical for result in results)
        meaning_failures = sum(
            not (result.inventory and result.devices and result.roundtrip)
            for result in results)
        print(f"  {holder}: CAUGHT; mechanical {mechanical_failures}/{len(results)}, "
              f"meaning {meaning_failures}/{len(results)}")

    print("negative matrix: PASS (every exact holder omission was caught)")
    print("structural refusals (a gap that breaks no pointer and still costs "
          "structure):")
    for note in structural:
        print(f"  refused: {note}")
    print("known blind spot: changing a key-table u16 to another valid action-list")
    print("  index can pass every check here while making a button do the wrong thing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
