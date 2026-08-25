#!/usr/bin/env python3
"""Read architecture-8 infrared durations by following stated addresses.

The walk is rooted at the exact pointer array that ``hconfig.decompile``
decodes in base section slot 5.  Its targets state the addresses of IR groups;
each group states record addresses; each record states its own start and its
duration-block addresses.  A block is read only from one of those addresses to
its zero-word terminator.  Nothing in this module scans for a byte pattern.

The command-line audit also proves the walk before reporting it: a frame is
accepted as NEC or Kaseikyo only when independently read leader timings and
the pulse-distance bit count agree with that protocol.

This is the reader FORMAT.md 5q needed and did not have. That section was
published while the tool behind half of it lived outside this repository, so
nobody reading the document could recompute its arch 8 numbers.
`verify_ir_spelling.py` does that now, using this module.

Usage:
    python tools/arch8_ir_duration_reader.py
    python tools/arch8_ir_duration_reader.py --json-output report.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Sequence

import _paths  # noqa: F401
import hconfig


REPO_ROOT = Path(__file__).resolve().parent.parent
IR_TABLE_SECTION = 5
IR_CLASS_STREAM = 1
IR_RECORD_POINTER_BIAS = 7
IR_HEADER_BASE = 12
IR_POINTERS_PER_GROUP = 3
IR_POINTER_GROUP_BYTES = 9
IR_MAX_POINTER_GROUPS = 16
IR_MAX_BLOCK_WORDS = 8192
IR_MARK = 0x8000
IR_DURATION_MAX = 0x7FFF


class Arch8IRReaderError(ValueError):
    """A stated address or a structure reached through it is invalid."""


@dataclass(frozen=True)
class Pulse:
    mark: bool
    microseconds: int


@dataclass(frozen=True)
class DurationBlock:
    address: int
    offset: int
    words: tuple[int, ...]
    byte_length: int

    def pulses(self) -> tuple[Pulse, ...]:
        out: list[Pulse] = []
        for word in self.words:
            mark = bool(word & IR_MARK)
            duration = word & IR_DURATION_MAX
            if out and out[-1].mark == mark:
                previous = out[-1]
                out[-1] = Pulse(mark, previous.microseconds + duration)
            else:
                out.append(Pulse(mark, duration))
        return tuple(out)


@dataclass(frozen=True)
class IRRecord:
    group_index: int
    command_index: int
    pointer_address: int
    pointer_offset: int
    start_address: int
    start_offset: int
    period_ns: int
    carrier_on_ns: int
    ir_class: int
    pointer_group_count: int
    block_addresses: tuple[int | None, ...]
    blocks: tuple[DurationBlock | None, ...]

    @property
    def carrier_hz(self) -> float | None:
        return None if self.period_ns == 0 else 1_000_000_000 / self.period_ns


@dataclass(frozen=True)
class IRGroup:
    index: int
    address: int
    offset: int
    byte_length: int
    records: tuple[IRRecord, ...]


@dataclass(frozen=True)
class Arch8IRDatabase:
    config_base: int
    root_offset: int
    groups: tuple[IRGroup, ...]
    unique_blocks: tuple[DurationBlock, ...]

    @property
    def records(self) -> tuple[IRRecord, ...]:
        return tuple(record for group in self.groups for record in group.records)


@dataclass(frozen=True)
class ProtocolSpec:
    name: str
    mark_min_us: int
    mark_max_us: int
    space_min_us: int
    space_max_us: int
    bits: int


PROTOCOLS = (
    ProtocolSpec("NEC", 8900, 9100, 4400, 4600, 32),
    ProtocolSpec("Kaseikyo", 3350, 3520, 1650, 1760, 48),
)


def _u16(blob: bytes, offset: int) -> int:
    if not 0 <= offset <= len(blob) - 2:
        raise Arch8IRReaderError(f"u16 read at 0x{offset:X} is outside the blob")
    return int.from_bytes(blob[offset:offset + 2], "little")


def _u24(blob: bytes, offset: int) -> int:
    if not 0 <= offset <= len(blob) - 3:
        raise Arch8IRReaderError(f"u24 read at 0x{offset:X} is outside the blob")
    return int.from_bytes(blob[offset:offset + 3], "little")


def _offset_of(blob: bytes, config_base: int, address: int, what: str) -> int:
    offset = address - config_base
    if not 0 <= offset < len(blob):
        raise Arch8IRReaderError(
            f"{what} address 0x{address:X} lands outside the blob"
        )
    return offset


def _read_block(
        blob: bytes, config_base: int, address: int,
        cache: dict[int, DurationBlock]) -> DurationBlock:
    cached = cache.get(address)
    if cached is not None:
        return cached
    offset = _offset_of(blob, config_base, address, "duration block")
    words: list[int] = []
    for index in range(IR_MAX_BLOCK_WORDS):
        at = offset + 2 * index
        word = _u16(blob, at)
        if word == 0:
            block = DurationBlock(address, offset, tuple(words), 2 * (index + 1))
            cache[address] = block
            return block
        words.append(word)
    raise Arch8IRReaderError(
        f"duration block at 0x{address:X} has no zero terminator within "
        f"{IR_MAX_BLOCK_WORDS} words"
    )


def _read_record(
        blob: bytes, config_base: int, pointer_address: int,
        group_index: int, command_index: int,
        block_cache: dict[int, DurationBlock]) -> IRRecord:
    pointer_offset = _offset_of(blob, config_base, pointer_address, "record")
    if pointer_offset + 4 > len(blob):
        raise Arch8IRReaderError(f"record pointer 0x{pointer_address:X} is truncated")
    ir_class = blob[pointer_offset]
    if ir_class != IR_CLASS_STREAM:
        raise Arch8IRReaderError(
            f"arch-8 record at 0x{pointer_address:X} has class {ir_class}, not 1"
        )

    start_address = _u24(blob, pointer_offset + 1)
    start_offset = _offset_of(blob, config_base, start_address, "record start")
    if pointer_address - start_address != IR_RECORD_POINTER_BIAS:
        raise Arch8IRReaderError(
            f"record pointer 0x{pointer_address:X} is not seven bytes into its "
            f"stated start 0x{start_address:X}"
        )
    if start_offset + IR_HEADER_BASE > len(blob):
        raise Arch8IRReaderError(f"record start 0x{start_address:X} is truncated")
    if blob[start_offset] != 0:
        raise Arch8IRReaderError(f"record at 0x{start_address:X} has a nonzero spare byte")
    if blob[start_offset + IR_RECORD_POINTER_BIAS] != ir_class:
        raise Arch8IRReaderError(f"record class disagrees at 0x{pointer_address:X}")

    period_ns = _u24(blob, start_offset + 1)
    carrier_on_ns = _u24(blob, start_offset + 4)
    pointer_group_count = blob[start_offset + 11]
    if not 1 <= pointer_group_count <= IR_MAX_POINTER_GROUPS:
        raise Arch8IRReaderError(
            f"record at 0x{start_address:X} states {pointer_group_count} pointer groups"
        )
    header_length = IR_HEADER_BASE + IR_POINTER_GROUP_BYTES * pointer_group_count
    if start_offset + header_length > len(blob):
        raise Arch8IRReaderError(f"record header at 0x{start_address:X} is truncated")

    addresses: list[int | None] = []
    blocks: list[DurationBlock | None] = []
    for slot in range(IR_POINTERS_PER_GROUP * pointer_group_count):
        address = _u24(blob, start_offset + IR_HEADER_BASE + 3 * slot)
        if address == 0:
            addresses.append(None)
            blocks.append(None)
            continue
        if address >= start_address:
            raise Arch8IRReaderError(
                f"record 0x{start_address:X} block 0x{address:X} is not backward"
            )
        addresses.append(address)
        blocks.append(_read_block(blob, config_base, address, block_cache))

    return IRRecord(
        group_index=group_index,
        command_index=command_index,
        pointer_address=pointer_address,
        pointer_offset=pointer_offset,
        start_address=start_address,
        start_offset=start_offset,
        period_ns=period_ns,
        carrier_on_ns=carrier_on_ns,
        ir_class=ir_class,
        pointer_group_count=pointer_group_count,
        block_addresses=tuple(addresses),
        blocks=tuple(blocks),
    )


def read_arch8_ir(blob: bytes, doc: dict) -> Arch8IRDatabase:
    """Follow the rooted section-5 address chain to every arch-8 duration."""
    headers = [
        region for region in doc["blob"]["regions"]
        if region["kind"] == "blob_header"
    ]
    if len(headers) != 1 or headers[0].get("architecture") != "arch 8":
        raise Arch8IRReaderError("hconfig did not identify exactly one arch-8 header")
    config_base = int(doc["blob"]["config_base"], 0)
    roots = [
        region for region in doc["blob"]["regions"]
        if region["kind"] == "pointer_table"
        and region.get("section") == IR_TABLE_SECTION
    ]
    if len(roots) != 1:
        raise Arch8IRReaderError(
            f"expected one exact pointer array in section 5, found {len(roots)}"
        )
    root = roots[0]
    if not root["targets"]:
        raise Arch8IRReaderError("the section-5 IR table is empty")

    block_cache: dict[int, DurationBlock] = {}
    groups: list[IRGroup] = []
    for group_index, group_offset in enumerate(root["targets"]):
        if not 0 <= group_offset <= len(blob) - 3:
            raise Arch8IRReaderError(f"IR group {group_index} is outside the blob")
        if blob[group_offset] != 0:
            raise Arch8IRReaderError(
                f"IR group {group_index} at 0x{group_offset:X} has a nonzero spare byte"
            )
        count = _u16(blob, group_offset + 1)
        if count == 0:
            raise Arch8IRReaderError(f"IR group {group_index} is empty")
        length = 3 + 3 * count
        if group_offset + length > len(blob):
            raise Arch8IRReaderError(f"IR group {group_index} is truncated")
        records: list[IRRecord] = []
        for command_index in range(count):
            address = _u24(blob, group_offset + 3 + 3 * command_index)
            records.append(_read_record(
                blob, config_base, address, group_index, command_index, block_cache,
            ))
        groups.append(IRGroup(
            index=group_index,
            address=config_base + group_offset,
            offset=group_offset,
            byte_length=length,
            records=tuple(records),
        ))

    return Arch8IRDatabase(
        config_base=config_base,
        root_offset=root["offset"],
        groups=tuple(groups),
        unique_blocks=tuple(sorted(block_cache.values(), key=lambda block: block.address)),
    )


def _pulse_distance_bits(pulses: tuple[Pulse, ...], start: int) -> int:
    """Count short mark/space bit cells until the first trailing gap."""
    bits = 0
    at = start
    while at + 1 < len(pulses):
        mark, space = pulses[at], pulses[at + 1]
        if not mark.mark or space.mark:
            break
        if not 250 <= mark.microseconds <= 1000:
            break
        if not 200 <= space.microseconds < 2000:
            break
        bits += 1
        at += 2
    return bits


def protocol_timing_proofs(database: Arch8IRDatabase) -> list[dict]:
    """Return frames whose leader and independent bit count name one protocol."""
    proofs: list[dict] = []
    for record in database.records:
        for pointer_group in range(record.pointer_group_count):
            slot = IR_POINTERS_PER_GROUP * pointer_group
            block = record.blocks[slot]
            if block is None:
                continue
            pulses = block.pulses()
            first_mark = next((i for i, pulse in enumerate(pulses) if pulse.mark), None)
            if first_mark is None or first_mark + 2 >= len(pulses):
                continue
            leader_mark = pulses[first_mark]
            leader_space = pulses[first_mark + 1]
            if leader_space.mark:
                continue
            bits = _pulse_distance_bits(pulses, first_mark + 2)
            for protocol in PROTOCOLS:
                if not protocol.mark_min_us <= leader_mark.microseconds <= protocol.mark_max_us:
                    continue
                if not protocol.space_min_us <= leader_space.microseconds <= protocol.space_max_us:
                    continue
                if bits != protocol.bits:
                    continue
                proofs.append({
                    "protocol": protocol.name,
                    "group_index": record.group_index,
                    "command_index": record.command_index,
                    "pointer_group": pointer_group,
                    "record_address": record.pointer_address,
                    "block_address": block.address,
                    "leader_mark_us": leader_mark.microseconds,
                    "leader_space_us": leader_space.microseconds,
                    "decoded_bits": bits,
                    "expected_bits": protocol.bits,
                    "tolerance_us": {
                        "leader_mark": [protocol.mark_min_us, protocol.mark_max_us],
                        "leader_space": [protocol.space_min_us, protocol.space_max_us],
                    },
                })
    return proofs


def prove_arch8_ir_reader(database: Arch8IRDatabase) -> dict:
    """Require a known-protocol closure before a caller measures durations."""
    proofs = protocol_timing_proofs(database)
    if not proofs:
        raise Arch8IRReaderError(
            "no decoded frame matched both known-protocol timing and bit count"
        )
    counts = Counter(proof["protocol"] for proof in proofs)
    near_38khz = [
        record for record in database.records
        if record.carrier_hz is not None and 37_500 <= record.carrier_hz <= 38_500
    ]
    example = proofs[0]
    record = next(
        record for record in database.records
        if record.pointer_address == example["record_address"]
    )
    return {
        "method": "known protocol leader timing and independent pulse-distance bit count",
        "passed": True,
        "matching_frame_count": len(proofs),
        "matching_frames_by_protocol": dict(sorted(counts.items())),
        "example": example,
        "near_38khz_record_count": len(near_38khz),
        "example_carrier_hz": record.carrier_hz,
        "example_carrier_period_ns": record.period_ns,
    }


def audit(samples: Path) -> dict:
    results = []
    for path in sorted(samples.glob("*.EZHex")):
        raw = path.read_bytes()
        _xml, _separator, blob = hconfig.split_container(raw)
        doc = hconfig.decompile(raw, filename=str(path))
        database = read_arch8_ir(blob, doc)
        proof = prove_arch8_ir_reader(database)
        results.append({
            "sample": path.name,
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "group_count": len(database.groups),
            "record_count": len(database.records),
            "unique_block_count": len(database.unique_blocks),
            "pointer_group_count_distribution": dict(sorted(Counter(
                str(record.pointer_group_count) for record in database.records
            ).items())),
            "proof": proof,
        })
    if not results:
        raise SystemExit(f"no EZHex samples found in {samples}")
    return {
        "reader": str(Path(__file__).resolve()),
        "address_route": [
            "hconfig exact pointer array in architecture base section 5",
            "section-5 target -> IR group",
            "IR-group target -> record class byte",
            "record-stated start -> pointer groups",
            "non-NULL pointer -> zero-terminated duration block",
        ],
        "byte_pattern_recognizer": False,
        "samples": results,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path,
                        help="a directory of arch 8 .EZHex containers")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    samples = (args.samples or REPO_ROOT / "samples" / "arch8").resolve()
    report = audit(samples)
    for sample in report["samples"]:
        proof = sample["proof"]
        example = proof["example"]
        print(
            f"PASS {sample['sample']}: {sample['group_count']} groups, "
            f"{sample['record_count']} records, {sample['unique_block_count']} unique blocks; "
            f"{proof['matching_frame_count']} known-protocol frames; example "
            f"{example['protocol']} {example['leader_mark_us']}/{example['leader_space_us']} us, "
            f"{example['decoded_bits']} bits"
        )
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {args.json_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
