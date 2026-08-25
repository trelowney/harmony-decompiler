"""Mechanical arch-9 blob relocation driven only by pointer_census.census.

The design is Danny Bloemendaal's, from ``harmony-explorations`` commit
``edb1349e669316320341e769c0434bb92c05571a``: shift, rewrite the census,
restamp ``end_addr`` first, and stamp the trailer checksum last.  This module
only returns bytes in memory and contains no filesystem or hardware write path.

The arch-9 floor is the first byte after the ``CMAH`` header marker (``0x5F``
in the public 525 sample), not Danny's Harmony One floor after a key table.  The
525 firmware loads section 6, indexes ``operand * 3 + 3``, and follows that u24,
so record placement is stated by the file rather than implied after the marker.
The bytes before this floor are the fixed header, 18 stated section addresses,
their trailing null/padding, and the marker; moving inside them would change the
container layout itself.
"""
from __future__ import annotations

import hconfig
from pointer_census import (
    CensusError,
    HOLDER_CLASSES,
    census,
    require_arch9,
)


class RelocateError(ValueError):
    """A bad insertion request or a census-backed relocation refusal."""


U24_MAX = 0xFFFFFF


def _kind_counts(blob: bytes) -> dict:
    counts = {}
    for region in hconfig.decompile(blob)["blob"]["regions"]:
        kind = region.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def containing_region(blob: bytes, at: int):
    """The decoded region an insertion point falls strictly inside, if any."""
    for region in hconfig.decompile(blob)["blob"]["regions"]:
        start = region.get("offset")
        if start is None:
            continue
        if start < at < start + hconfig.region_length(region):
            return region
    return None


def survives_relocation(before: bytes, after: bytes) -> dict:
    """Kinds the decompiler stops recognising once the gap is in the file.

    This is the guard the census cannot be. The census is the thing relocation
    rewrites, so asking it whether a relocation worked asks a field about
    itself - the fault this project has published four times. The decompiler is
    a second reader with its own grammars, and inserting a run of zero bytes
    adds no structure to a config, so **nothing it recognised before may stop
    being recognised after**.

    Growth is expected and allowed: a gap shows up as more opaque bytes, and as
    one more section-level chunk when it lands on a section boundary. Loss is
    not, and loss is what a bad insertion point causes - splitting an object, or
    padding a span whose reader walks it from one end.
    """
    old, new = _kind_counts(before), _kind_counts(after)
    return {kind: (count, new.get(kind, 0))
            for kind, count in old.items() if new.get(kind, 0) < count}


def relocation_floor(blob: bytes) -> int:
    """Return the first insertable byte, immediately after arch 9's header."""
    try:
        require_arch9(blob)
    except CensusError as exc:
        raise RelocateError(str(exc)) from exc
    marker = blob.find(hconfig.HEADER_MARKER)
    if marker < 0:
        raise RelocateError("arch 9 / protocol 9 CMAH header marker is absent")
    return marker + len(hconfig.HEADER_MARKER)


def relocate(blob: bytes, at: int, delta: int, omit: str | None = None) -> bytes:
    """Insert zero bytes and rewrite every moved reader-backed address field.

    ``omit`` is a test-only fault injection: it suppresses one holder class so
    ``verify_arch9_relocation.py`` can prove the complete check detects it.
    """
    if not isinstance(blob, bytes):
        raise TypeError("relocate expects an immutable bytes blob")
    if isinstance(at, bool) or not isinstance(at, int):
        raise RelocateError(f"insertion offset must be an integer, not {at!r}")
    if isinstance(delta, bool) or not isinstance(delta, int) or delta <= 0:
        raise RelocateError(f"delta must be a positive integer, not {delta!r}")
    if omit is not None and omit not in HOLDER_CLASSES:
        raise RelocateError(f"unknown test-only holder omission {omit!r}")

    floor = relocation_floor(blob)
    trailer = len(blob) - hconfig.TRAILER_CHECKSUM_OFFSET
    if at < floor or at > trailer:
        raise RelocateError(
            f"insertion at {at} is outside content [{floor}, {trailer}]")

    try:
        entries = census(blob)
    except CensusError as exc:
        raise RelocateError(f"pointer census refused relocation: {exc}") from exc
    for entry in entries:
        if entry["at"] < at < entry["at"] + entry["width"]:
            raise RelocateError(
                f"insertion at {at} splits {entry['holder']} field "
                f"at {entry['at']}")
        if (entry["width"] == 3 and entry["lands"] is not None
                and entry["lands"] >= at
                and entry["target"] + delta > U24_MAX):
            raise RelocateError(
                f"{entry['holder']} would state 0x{entry['target'] + delta:X}, "
                "past a u24")

    shifted = bytearray(len(blob) + delta)
    shifted[:at] = blob[:at]
    shifted[at:at + delta] = bytes(delta)
    shifted[at + delta:] = blob[at:]

    for entry in entries:
        if entry["lands"] is None or entry["lands"] < at:
            continue
        if omit == entry["holder"]:
            continue
        if entry["at"] == 4:
            # end_addr is intentionally written in the ordered restamp below.
            continue
        field_at = entry["at"] + (delta if entry["at"] >= at else 0)
        shifted[field_at:field_at + entry["width"]] = (
            entry["target"] + delta).to_bytes(entry["width"], "little")

    # end_addr must precede the checksum because trailer_checksum covers it.
    if omit != "blob_header":
        end_addr = int.from_bytes(blob[4:8], "little") + delta
        shifted[4:8] = end_addr.to_bytes(4, "little")
    checksum_at = len(shifted) - hconfig.TRAILER_CHECKSUM_OFFSET
    shifted[checksum_at:checksum_at + 2] = (
        hconfig.trailer_checksum(shifted).to_bytes(2, "little"))
    out = bytes(shifted)

    # The census says every stated address was rewritten. It cannot say the
    # gap went somewhere a config can hold one, because a gap in the wrong
    # place breaks no pointer. Ask the decompiler instead - see
    # survives_relocation. Fault injection skips this deliberately: an omitted
    # holder class is meant to produce a broken blob for the negative matrix.
    if omit is None:
        lost = survives_relocation(blob, out)
        if lost:
            where = containing_region(blob, at)
            detail = ", ".join(f"{kind} {before} -> {after}"
                               for kind, (before, after) in sorted(lost.items()))
            site = (f"; {at} is inside the {where['kind']} at {where['offset']}"
                    if where else
                    f"; {at} splits no object, so the gap has padded a span "
                    f"whose reader walks it end to end")
            raise RelocateError(
                f"insertion at {at} costs the decompiler structure it read "
                f"before: {detail}{site}")
    return out


__all__ = ["RelocateError", "containing_region", "relocate",
           "relocation_floor", "survives_relocation"]
