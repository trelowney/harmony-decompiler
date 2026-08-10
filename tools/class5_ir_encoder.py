"""Build a self-contained Harmony arch-9 class-5 IR record, offline only.

This module deliberately solves the byte-packing layer and no more.  Its input
is one or more already-normalised streams of Harmony duration words, with one
stream (or ``None``) for every pointer slot in the record header.  It does not
infer press/repeat slot semantics from a single physical capture and it does not
place anything into a real configuration.

The class-5 body/table/block layout was published and firmware-verified by
Danny Bloemendaal in ``harmony-explorations`` (commit a6516c7).  The bounded
literal packing strategy, relocatable builder and independent decoder here are
from the trelowney Harmony project.  Literal packing uses one symbol for every
unique complete stream.  It is intentionally simple rather than size-optimal.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


IR_CLASS_ARCH9 = 5
IR_POINTERS_PER_GROUP = 3
IR_HEADER_BASE = 12
IR_HEADER_GROUP = 9
IR_RECORD_POINTER_BIAS = 7
IR_MAX_GROUPS = 16
IR_SYMBOL_LIMIT = 0xFF
IR_SYMBOL_WORD_LIMIT = 8192
IR_DURATION_MAX = 0x7FFF
IR_MARK = 0x8000
IR_CARRIER_MAX_NS = 256_000
ADDRESS_LIMIT = 1 << 24


class Class5EncodeError(ValueError):
    """The requested record cannot be represented within the pinned rails."""


class Class5DecodeError(ValueError):
    """A generated class-5 record violates its own structural contract."""


@dataclass(frozen=True)
class Pulse:
    mark: bool
    microseconds: int


@dataclass(frozen=True)
class EncodedClass5Record:
    """A relocatable, self-contained record and its internal addresses."""

    blob: bytes
    base_address: int
    record_address: int
    header_address: int
    table_address: int
    pointer_body_addresses: tuple[int | None, ...]
    symbol_addresses: tuple[int, ...]
    body_addresses: tuple[int, ...]
    period_ns: int

    @property
    def on_ns(self) -> int:
        return self.period_ns >> 1

    @property
    def group_count(self) -> int:
        return len(self.pointer_body_addresses) // IR_POINTERS_PER_GROUP

    def manifest(self) -> dict:
        return {
            "base_address": self.base_address,
            "record_address": self.record_address,
            "header_address": self.header_address,
            "table_address": self.table_address,
            "pointer_body_addresses": list(self.pointer_body_addresses),
            "symbol_addresses": list(self.symbol_addresses),
            "body_addresses": list(self.body_addresses),
            "period_ns": self.period_ns,
            "on_ns": self.on_ns,
            "group_count": self.group_count,
            "byte_length": len(self.blob),
        }


@dataclass(frozen=True)
class DecodedClass5Record:
    period_ns: int
    on_ns: int
    pointer_streams: tuple[tuple[int, ...] | None, ...]
    pointer_body_addresses: tuple[int | None, ...]
    table_addresses: tuple[int | None, ...]


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Class5EncodeError(f"{label} must be an integer")
    return value


def _put24(out: bytearray, offset: int, value: int) -> None:
    if not 0 <= value < ADDRESS_LIMIT:
        raise Class5EncodeError(f"address 0x{value:X} does not fit u24")
    out[offset:offset + 3] = value.to_bytes(3, "little")


def _normalise_stream(stream: Sequence[int], label: str) -> tuple[int, ...]:
    if not stream:
        raise Class5EncodeError(f"{label} is empty")
    if len(stream) > IR_SYMBOL_WORD_LIMIT:
        raise Class5EncodeError(
            f"{label} has {len(stream)} words, above the {IR_SYMBOL_WORD_LIMIT} safety limit"
        )
    words = []
    for index, raw in enumerate(stream):
        word = _integer(raw, f"{label}[{index}]")
        if not 1 <= word <= 0xFFFF:
            raise Class5EncodeError(f"{label}[{index}]={word} is not a nonzero u16 duration word")
        words.append(word)
    return tuple(words)


def period_for_hz(hertz: int | float) -> int:
    """Store a carrier by truncating its nanosecond period, as the corpus does."""
    if isinstance(hertz, bool) or not isinstance(hertz, (int, float)):
        raise Class5EncodeError("carrier frequency must be numeric")
    if not math.isfinite(hertz) or hertz <= 0:
        raise Class5EncodeError("carrier frequency must be positive and finite")
    period = math.floor(1_000_000_000 / hertz)
    if not 1 <= period <= IR_CARRIER_MAX_NS:
        raise Class5EncodeError(
            f"{hertz} Hz produces {period} ns, outside 1..{IR_CARRIER_MAX_NS}"
        )
    return period


def words_from_pulses(pulses: Sequence[Pulse]) -> tuple[int, ...]:
    """Turn RAW pulses into duration words, splitting long spaces losslessly.

    The public 525 corpus spells long gaps as consecutive ``0x7FFF`` spaces.
    Long marks are refused: splitting one may restart carrier phase and that has
    not been proven equivalent.  Zero-duration entries are also refused.
    """
    if not pulses:
        raise Class5EncodeError("pulse stream is empty")
    words: list[int] = []
    for index, pulse in enumerate(pulses):
        if not isinstance(pulse, Pulse):
            raise Class5EncodeError(f"pulse {index} is not a Pulse")
        if not isinstance(pulse.mark, bool):
            raise Class5EncodeError(f"pulse {index} mark flag is not boolean")
        duration = _integer(pulse.microseconds, f"pulse {index} duration")
        if duration <= 0:
            raise Class5EncodeError(f"pulse {index} duration must be positive")
        if pulse.mark and duration > IR_DURATION_MAX:
            raise Class5EncodeError(
                f"pulse {index} is a {duration} us mark; long-mark splitting is unproven"
            )
        while duration > IR_DURATION_MAX:
            words.append(IR_DURATION_MAX)
            duration -= IR_DURATION_MAX
        if duration:
            words.append(duration | (IR_MARK if pulse.mark else 0))
    return tuple(words)


def pulses_from_words(words: Sequence[int], *, coalesce: bool = False) -> tuple[Pulse, ...]:
    """Decode words; optionally recombine adjacent chunks with the same state."""
    stream = _normalise_stream(words, "word stream")
    pulses = [Pulse(bool(word & IR_MARK), word & IR_DURATION_MAX) for word in stream]
    if any(pulse.microseconds == 0 for pulse in pulses):
        raise Class5DecodeError("a duration word carries zero microseconds")
    if not coalesce:
        return tuple(pulses)
    merged: list[Pulse] = []
    for pulse in pulses:
        if merged and merged[-1].mark == pulse.mark:
            merged[-1] = Pulse(pulse.mark, merged[-1].microseconds + pulse.microseconds)
        else:
            merged.append(pulse)
    return tuple(merged)


def _symbol_block(words: tuple[int, ...]) -> bytes:
    out = bytearray(4 + 2 * len(words))
    out[0:2] = len(words).to_bytes(2, "little")
    for index, word in enumerate(words):
        out[2 + 2 * index:4 + 2 * index] = word.to_bytes(2, "little")
    # The final zero word is already present in the zero-initialised bytearray.
    return bytes(out)


def encode_class5_record(
    *,
    period_ns: int,
    pointer_streams: Sequence[Sequence[int] | None],
    base_address: int,
) -> EncodedClass5Record:
    """Pack duration streams into one self-contained arch-9 class-5 record.

    ``pointer_streams`` preserves the header's slots, including NULLs.  Its
    length must be a whole number of three-pointer groups.  Identical non-NULL
    streams share both a body and a symbol.  All internal pointers are absolute
    u24 addresses derived from ``base_address``.
    """
    period = _integer(period_ns, "carrier period")
    base = _integer(base_address, "base address")
    if not 1 <= period <= IR_CARRIER_MAX_NS:
        raise Class5EncodeError(
            f"carrier period {period} ns is outside 1..{IR_CARRIER_MAX_NS}"
        )
    if not 1 <= base < ADDRESS_LIMIT:
        raise Class5EncodeError(
            f"base address 0x{base:X} is outside 1..0x{ADDRESS_LIMIT - 1:X}; zero is NULL"
        )
    if not pointer_streams or len(pointer_streams) % IR_POINTERS_PER_GROUP:
        raise Class5EncodeError("pointer streams must contain whole three-slot groups")
    groups = len(pointer_streams) // IR_POINTERS_PER_GROUP
    if groups > IR_MAX_GROUPS:
        raise Class5EncodeError(f"{groups} pointer groups exceed the {IR_MAX_GROUPS} safety limit")

    normalised: list[tuple[int, ...] | None] = []
    unique: list[tuple[int, ...]] = []
    symbol_index: dict[tuple[int, ...], int] = {}
    for slot, stream in enumerate(pointer_streams):
        if stream is None:
            normalised.append(None)
            continue
        words = _normalise_stream(stream, f"pointer stream {slot}")
        normalised.append(words)
        if words not in symbol_index:
            symbol_index[words] = len(unique)
            unique.append(words)
    if not unique:
        raise Class5EncodeError("a class-5 record must contain at least one non-NULL stream")
    if len(unique) > IR_SYMBOL_LIMIT:
        raise Class5EncodeError(f"{len(unique)} symbols do not fit the table count byte")

    cursor = base
    parts: list[bytes] = []
    symbol_addresses: list[int] = []
    for words in unique:
        block = _symbol_block(words)
        symbol_addresses.append(cursor)
        parts.append(block)
        cursor += len(block)

    table_address = cursor
    table = bytearray(1 + 3 * len(unique))
    table[0] = len(unique)
    for index, address in enumerate(symbol_addresses):
        _put24(table, 1 + 3 * index, address)
    parts.append(bytes(table))
    cursor += len(table)

    body_addresses: list[int] = []
    for index in range(len(unique)):
        body_addresses.append(cursor)
        body = bytearray(6)
        _put24(body, 0, table_address)
        body[3:5] = (1).to_bytes(2, "little")
        body[5] = index
        parts.append(bytes(body))
        cursor += len(body)

    pointer_body_addresses = tuple(
        None if stream is None else body_addresses[symbol_index[stream]]
        for stream in normalised
    )

    header_address = cursor
    header = bytearray(IR_HEADER_BASE + IR_HEADER_GROUP * groups)
    header[1:4] = period.to_bytes(3, "little")
    header[4:7] = (period >> 1).to_bytes(3, "little")
    header[7] = IR_CLASS_ARCH9
    _put24(header, 8, header_address)
    header[11] = groups
    for slot, address in enumerate(pointer_body_addresses):
        _put24(header, IR_HEADER_BASE + 3 * slot, address or 0)
    parts.append(bytes(header))
    cursor += len(header)

    if cursor > ADDRESS_LIMIT:
        raise Class5EncodeError(
            f"record ending at 0x{cursor:X} exceeds the 24-bit address space"
        )
    blob = b"".join(parts)
    if len(blob) != cursor - base:
        raise AssertionError("internal placement length mismatch")
    return EncodedClass5Record(
        blob=blob,
        base_address=base,
        record_address=header_address + IR_RECORD_POINTER_BIAS,
        header_address=header_address,
        table_address=table_address,
        pointer_body_addresses=pointer_body_addresses,
        symbol_addresses=tuple(symbol_addresses),
        body_addresses=tuple(body_addresses),
        period_ns=period,
    )


def decode_class5_record(blob: bytes, *, base_address: int, record_address: int) -> DecodedClass5Record:
    """Independently expand a self-contained record back to its slot streams."""
    if not isinstance(blob, bytes):
        raise Class5DecodeError("blob must be immutable bytes")

    def offset(address: int, size: int = 1) -> int:
        at = address - base_address
        if at < 0 or size < 0 or at + size > len(blob):
            raise Class5DecodeError(
                f"address 0x{address:06X} size {size} leaves the generated blob"
            )
        return at

    def u16(address: int) -> int:
        at = offset(address, 2)
        return int.from_bytes(blob[at:at + 2], "little")

    def u24(address: int) -> int:
        at = offset(address, 3)
        return int.from_bytes(blob[at:at + 3], "little")

    record_at = offset(record_address, 4)
    if blob[record_at] != IR_CLASS_ARCH9:
        raise Class5DecodeError(f"record class is {blob[record_at]}, expected {IR_CLASS_ARCH9}")
    header_address = int.from_bytes(blob[record_at + 1:record_at + 4], "little")
    if header_address + IR_RECORD_POINTER_BIAS != record_address:
        raise Class5DecodeError("record pointer does not land seven bytes into its own header")
    header_at = offset(header_address, IR_HEADER_BASE)
    period = int.from_bytes(blob[header_at + 1:header_at + 4], "little")
    on_ns = int.from_bytes(blob[header_at + 4:header_at + 7], "little")
    groups = blob[header_at + 11]
    if not 1 <= groups <= IR_MAX_GROUPS:
        raise Class5DecodeError(f"invalid pointer group count {groups}")
    offset(header_address, IR_HEADER_BASE + IR_HEADER_GROUP * groups)

    pointers: list[int | None] = []
    streams: list[tuple[int, ...] | None] = []
    tables: list[int | None] = []
    for slot in range(groups * IR_POINTERS_PER_GROUP):
        pointer = u24(header_address + IR_HEADER_BASE + 3 * slot)
        if pointer == 0:
            pointers.append(None)
            streams.append(None)
            tables.append(None)
            continue
        pointers.append(pointer)
        table_address = u24(pointer)
        index_count = u16(pointer + 3)
        body_at = offset(pointer + 5, index_count)
        indices = blob[body_at:body_at + index_count]
        table_at = offset(table_address)
        symbol_count = blob[table_at]
        offset(table_address, 1 + 3 * symbol_count)
        words: list[int] = []
        for index in indices:
            if index >= symbol_count:
                raise Class5DecodeError(f"body index {index} leaves {symbol_count}-entry table")
            symbol_address = u24(table_address + 1 + 3 * index)
            count = u16(symbol_address)
            if count > IR_SYMBOL_WORD_LIMIT:
                raise Class5DecodeError(f"symbol has {count} words above the safety limit")
            pulse_at = offset(symbol_address + 2, 2 * count + 2)
            symbol_words = tuple(
                int.from_bytes(blob[p:p + 2], "little")
                for p in range(pulse_at, pulse_at + 2 * count, 2)
            )
            if int.from_bytes(blob[pulse_at + 2 * count:pulse_at + 2 * count + 2], "little") != 0:
                raise Class5DecodeError("symbol block is missing its trailing zero word")
            words.extend(symbol_words)
        streams.append(tuple(words))
        tables.append(table_address)
    return DecodedClass5Record(
        period_ns=period,
        on_ns=on_ns,
        pointer_streams=tuple(streams),
        pointer_body_addresses=tuple(pointers),
        table_addresses=tuple(tables),
    )
