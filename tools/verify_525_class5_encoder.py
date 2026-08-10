"""Prove literal class-5 packing against six learned X96 signals, offline only.

The source configurations are read but never modified.  With ``--bundle`` this
writes self-contained synthetic records and their expected expanded streams to
JSON so a second implementation can audit them.  Existing output is refused.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

import _paths
import analyze_525_ir as source
import verify_525_semantics as semantics
from class5_ir_encoder import (
    Class5EncodeError,
    IR_DURATION_MAX,
    IR_MARK,
    IR_SYMBOL_WORD_LIMIT,
    Pulse,
    decode_class5_record,
    encode_class5_record,
    period_for_hz,
    pulses_from_words,
    words_from_pulses,
)


GOLDENS = {
    9: (0x01, 0xFE, 0x4E, 0xB1),
    19: (0x01, 0xFE, 0x0C, 0xF3),
    27: (0x01, 0xFE, 0x59, 0xA6),
    29: (0x01, 0xFE, 0x01, 0xFE),
    41: (0x01, 0xFE, 0x0D, 0xF2),
    56: (0x01, 0xFE, 0x4A, 0xB5),
}


def raw_record(blob: bytes, address: int) -> tuple[int, list[int]]:
    start = address - source.BASE - 7
    assert blob[start] == 0 and blob[start + 7] == source.IR_CLASS_525
    assert source.u24(blob, start + 8) == address - 7
    groups = blob[start + 11]
    pointers = [source.u24(blob, start + 12 + 3 * slot) for slot in range(3 * groups)]
    return source.u24(blob, start + 1), pointers


def expect_error(label: str, operation) -> str:
    try:
        operation()
    except Class5EncodeError:
        return label
    raise AssertionError(f"rail did not refuse {label}")


def verify_rails() -> list[str]:
    sample = (IR_MARK | 9000, 4500, IR_MARK | 560, 560)
    rails = [
        expect_error("no pointer groups", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[], base_address=0x60000)),
        expect_error("partial pointer group", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[sample, None], base_address=0x60000)),
        expect_error("all NULL pointers", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[None, None, None], base_address=0x60000)),
        expect_error("seventeen groups", lambda: encode_class5_record(
            period_ns=26_315,
            pointer_streams=[sample] + [None] * 50,
            base_address=0x60000)),
        expect_error("zero carrier", lambda: encode_class5_record(
            period_ns=0, pointer_streams=[sample, None, None], base_address=0x60000)),
        expect_error("NULL base", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[sample, None, None], base_address=0)),
        expect_error("empty stream", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[(), None, None], base_address=0x60000)),
        expect_error("zero duration word", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[(0,), None, None], base_address=0x60000)),
        expect_error("oversized duration word", lambda: encode_class5_record(
            period_ns=26_315, pointer_streams=[(0x10000,), None, None], base_address=0x60000)),
        expect_error("oversized symbol", lambda: encode_class5_record(
            period_ns=26_315,
            pointer_streams=[(1,) * (IR_SYMBOL_WORD_LIMIT + 1), None, None],
            base_address=0x60000)),
        expect_error("zero RAW duration", lambda: words_from_pulses([Pulse(False, 0)])),
        expect_error("unproven long mark", lambda: words_from_pulses([
            Pulse(True, IR_DURATION_MAX + 1)])),
    ]
    long_gap = 2 * IR_DURATION_MAX + 2
    split = words_from_pulses([Pulse(False, long_gap)])
    assert split == (IR_DURATION_MAX, IR_DURATION_MAX, 2)
    assert pulses_from_words(split, coalesce=True) == (Pulse(False, long_gap),)
    assert period_for_hz(37_853) == 26_417
    return rails


def verify_duplicate_sharing() -> None:
    stream = (IR_MARK | 9000, 4500, IR_MARK | 560, 560)
    encoded = encode_class5_record(
        period_ns=period_for_hz(38_000),
        pointer_streams=[stream, stream, None],
        base_address=0x60000,
    )
    assert len(encoded.symbol_addresses) == 1
    assert len(encoded.body_addresses) == 1
    assert encoded.pointer_body_addresses[0] == encoded.pointer_body_addresses[1]
    decoded = decode_class5_record(
        encoded.blob,
        base_address=encoded.base_address,
        record_address=encoded.record_address,
    )
    assert decoded.pointer_streams == (stream, stream, None)


def build_vectors() -> list[dict]:
    blob = _paths.get_blob(_paths.SAMPLE_BLOB)
    sections = semantics.section_offsets(blob)
    groups = source.ir_groups(blob, sections)
    vectors = []
    for sequence, (command, expected_frame) in enumerate(GOLDENS.items()):
        address = groups[3][command]
        period_ns, pointers = raw_record(blob, address)
        streams = [None if pointer == 0 else tuple(source.body(blob, pointer)["words"])
                   for pointer in pointers]
        assert len(streams) == 3 and streams[0] is not None and streams[2] is None
        assert source.nec_frame(list(streams[0])) == expected_frame

        encoded = encode_class5_record(
            period_ns=period_ns,
            pointer_streams=streams,
            base_address=0x60000 + sequence * 0x1000,
        )
        decoded = decode_class5_record(
            encoded.blob,
            base_address=encoded.base_address,
            record_address=encoded.record_address,
        )
        assert decoded.period_ns == period_ns
        assert decoded.on_ns == period_ns >> 1
        assert decoded.pointer_streams == tuple(streams)
        assert source.nec_frame(list(decoded.pointer_streams[0])) == expected_frame
        assert len(encoded.symbol_addresses) == len({stream for stream in streams if stream})
        assert {
            address for address in encoded.pointer_body_addresses if address
        } == set(encoded.body_addresses)

        vectors.append({
            "x96_command": command,
            "expected_nec": list(expected_frame),
            "period_ns": period_ns,
            "on_ns": period_ns >> 1,
            "expected_streams": [None if stream is None else list(stream) for stream in streams],
            "blob_base64": base64.b64encode(encoded.blob).decode("ascii"),
            "blob_sha256": hashlib.sha256(encoded.blob).hexdigest(),
            "manifest": encoded.manifest(),
            "local_decode_exact": True,
        })
    return vectors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path,
                        help="write generated vectors for an independent reader; refuse overwrite")
    args = parser.parse_args()

    rails = verify_rails()
    verify_duplicate_sharing()
    vectors = build_vectors()
    proof = {
        "schema": 1,
        "scope": "isolated class-5 literal packing; no config placement or learning semantics",
        "source": "public Harmony 525 X96 group 3",
        "credit": {
            "class5_structure": "Danny Bloemendaal, harmony-explorations a6516c7",
            "literal_packer_and_golden_harness": "trelowney Harmony project",
        },
        "rails": rails,
        "duplicate_sharing": True,
        "vectors": vectors,
    }
    if args.bundle:
        args.bundle.parent.mkdir(parents=True, exist_ok=True)
        with args.bundle.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(proof, handle, indent=2)
            handle.write("\n")
        print(f"wrote {args.bundle}")
    print(f"PASS: {len(vectors)} X96 golden vectors, {len(rails)} refusal rails")
    for vector in vectors:
        print(
            f"  command {vector['x96_command']:02d}: NEC "
            + " ".join(f"{value:02X}" for value in vector["expected_nec"])
            + f", {vector['manifest']['byte_length']} bytes, "
            + vector["blob_sha256"][:16]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
