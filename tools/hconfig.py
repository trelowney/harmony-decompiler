"""Decompile a Harmony config to JSON and compile it back.

The model is deliberately blunt: a config is an **ordered list of regions that
tiles the blob completely**, with no gaps and no overlaps. A region is either
something understood, which is stored as fields and re-emitted from them, or
something not yet understood, which is stored as hex and passed straight
through. Every structure that gets decoded moves one region from the second
category to the first, and the round-trip test says immediately whether the
decoding was right.

That test is the point. Decompile a config, compile it back, compare bytes. If
they match, the model is complete for that file - not plausible, complete. It is
the only feedback loop available on a format with no documentation.

Pointers decompile as `region + delta` and are resolved against the new layout on
compile, so a config can change length and everything relinks. What that cannot
cover is a pointer still buried in a region nobody has decoded: it gets copied as
hex and left aimed at whatever moved into its place. Some of those have been
found and pulled out - see find_references - but assume there are more, and see
docs/OPEN-QUESTIONS.md.

Layout of the arch 9 blob:

    0x00000  'AHCM'
    0x00004  u32  absolute flash address of the end marker
    0x00008  u32  unknown (0x1400)
    0x0000C  u32[18]  absolute section addresses
    0x00054  7 bytes of padding
    0x0005B  'CMAH'
    0x0005F  data, not covered by the section table (the 114 records live here)
    0x0F35B  section 0 .. section 17, contiguous
    0x13292  'MCHA'

Container per libconcord/operationfile.cpp:find_config_binary.
"""
import hashlib
import json
import re
from pathlib import Path

FORMAT_VERSION = 1

END_TAG = b"</INFORMATION>"
CONFIG_BASE = 0x20000
# Each architecture stamps the blob with its own magic, its own end marker and
# its own end-of-header marker. Arch 9 mirrors its magic for the end marker;
# arch 8 does not mirror anything, which is worth knowing before assuming a
# pattern from one example.
ARCHITECTURES = {
    b"AHCM": {"end": b"MCHA", "marker": b"CMAH", "name": "arch 9"},
    b"TPTP": {"end": b"DKDK", "marker": b"WLWL", "name": "arch 8"},
}

MAGIC = b"AHCM"
END_MAGIC = b"MCHA"
HEADER_MARKER = b"CMAH"
PTR_TABLE_OFF = 0x0C
HEX_LINE = 32          # bytes of hex per line, so diffs stay readable


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------
# helpers

def _hex_lines(data: bytes):
    return [data[i:i + HEX_LINE].hex() for i in range(0, len(data), HEX_LINE)]


def _unhex(lines):
    return bytes.fromhex("".join(lines))


def _u32(data, off):
    return int.from_bytes(data[off:off + 4], "little")


def _p32(v):
    return v.to_bytes(4, "little")


def blob_checksum(blob: bytes) -> int:
    c = 0x69
    for b in blob:
        c ^= b
    return c


TRAILER_CHECKSUM_SEED = 0x4321
TRAILER_CHECKSUM_OFFSET = 6


def trailer_checksum(blob: bytes) -> int:
    """Firmware-validated u16 XOR stored immediately before the end marker.

    The 525 boot validator loads 0x4321 at 0x04E8A, XORs successive little
    endian words at 0x04EF8..0x04F02, and compares the stored two bytes at
    0x04F54..0x04F5C. The checksum excludes itself and the four-byte marker.
    """
    if len(blob) < TRAILER_CHECKSUM_OFFSET:
        raise ConfigError("blob is too short to hold its trailer checksum")
    accumulator = TRAILER_CHECKSUM_SEED
    end = len(blob) - TRAILER_CHECKSUM_OFFSET
    for offset in range(0, end - 1, 2):
        accumulator ^= blob[offset] | (blob[offset + 1] << 8)
    return accumulator


def split_container(raw: bytes):
    """Split an .EZHex into (xml_bytes, separator, blob). Tolerates a bare blob."""
    i = raw.find(END_TAG)
    if i == -1:
        return None, b"", raw
    xml_end = i + len(END_TAG)
    return raw[:xml_end], raw[xml_end:xml_end + 2], raw[xml_end + 2:]


def _tag(xml: bytes, name: str):
    m = re.search(rf"<{name}>(.*?)</{name}>".encode(), xml, re.S)
    return m.group(1).decode("ascii", "replace").strip() if m else None


def _set_tag(xml: bytes, name: str, value) -> bytes:
    """Replace a tag's contents, preserving every other byte."""
    pat = re.compile(rf"(<{name}>)(.*?)(</{name}>)".encode(), re.S)
    if not pat.search(xml):
        return xml
    return pat.sub(lambda m: m.group(1) + str(value).encode() + m.group(3), xml, count=1)


# --------------------------------------------------------------------------
# recognisers
#
# Each one takes the whole blob plus the span of an opaque region and returns
# the structures it can identify inside it. Adding a recogniser is how a region
# moves from "hex we pass through" to "fields we re-emit", and the round-trip
# test grades the result immediately.

KEY_TERM = 0x7F
KEY_MIN_ENTRIES = 15
KEY_MIN_UNIQUE = 0.9


def find_key_tables(blob: bytes, start: int, end: int):
    """Runs of <u8 code> <u16 target> <0x7F>.

    Ranked on the proportion of distinct codes rather than length, because the
    longest 0x7F-terminated runs in a config are always filler. A real table has
    every code distinct - see tools/find_keytables.py, which this mirrors.
    """
    spans = []
    # stride 5 first: it is the rarer and more specific shape, and a 5-byte
    # table read at stride 4 dissolves into nothing, so there is no contest
    for stride in (5, 4):
        code_at = 1 if stride == 5 else 0
        i = start
        while i + stride <= end:
            if blob[i + stride - 1] != KEY_TERM:
                i += 1
                continue
            run_start, count = i, 0
            while i + stride <= end and blob[i + stride - 1] == KEY_TERM:
                count += 1
                i += stride
            if count < KEY_MIN_ENTRIES:
                continue
            if any(run_start < s + c * st and s < run_start + count * stride
                   for s, c, st in spans):
                continue
            codes = [blob[run_start + k * stride + code_at] for k in range(count)]
            if len(set(codes)) / count < KEY_MIN_UNIQUE:
                continue
            spans.append((run_start, count, stride))
    return sorted(spans)


def _key_table_region(blob, off, count, parent=None, stride=4):
    """One key table.

    Two shapes exist. The common one is `<u8 code> <u16 target> <0x7F>`. The
    other carries a leading byte per entry, `<u8 flag> <u8 code> <u16 target>
    <0x7F>`, and the one instance found so far has the flag set to 1 throughout.

    That 5-byte table is worth the special case: it holds the same 50 physical
    key codes in the same order as the main table, and is preceded by a count
    byte of 0x33 = 51 exactly as the main table is.
    """
    entries = []
    for k in range(count):
        p = off + k * stride
        e = {}
        if stride == 5:
            e["flag"] = f"0x{blob[p]:02X}"
        e["code"] = f"0x{blob[p + stride - 4]:02X}"
        e["target"] = int.from_bytes(blob[p + stride - 3:p + stride - 1], "little")
        entries.append(e)
    region = {
        "kind": "key_table",
        "offset": off,
        "length": count * stride,
        "entry_stride": stride,
        "terminator": f"0x{KEY_TERM:02X}",
        "entries": entries,
    }
    if parent and "section" in parent:
        region["section"] = parent["section"]
    return region


NAME_MAGIC = b"\xED\xFE"        # 0xFEED
NAME_END = b"\xEF\xBE"          # 0xBEEF
NAME_REC = 0xA7


def parse_name_table(blob: bytes, start: int, end: int):
    """The named-symbol table of the "HarmonyAssistant" rule engine.

        ED FE      0xFEED
        u16        offset of the terminator, from the start of the table
        u8         unknown, 0 here
        records:   A7 <u16 len> <u16 parent> <u16 index> <char name[len-4]>
        EF BE      0xBEEF

    `len` counts the parent and index fields plus the name, so a record occupies
    len+3 bytes. The u16 after the magic is checked against where the terminator
    actually lands, which is what makes this safe to re-emit rather than a guess.

    Returns None if the span is not a name table.
    """
    if blob[start:start + 2] != NAME_MAGIC:
        return None
    declared = int.from_bytes(blob[start + 2:start + 4], "little")
    term = start + declared
    if term + 2 > end or blob[term:term + 2] != NAME_END:
        return None

    records, off = [], start + 5
    while off < term:
        if blob[off] != NAME_REC:
            return None
        ln = int.from_bytes(blob[off + 1:off + 3], "little")
        if ln < 4 or off + 3 + ln > term:
            return None
        records.append({
            "parent": int.from_bytes(blob[off + 3:off + 5], "little"),
            "index": int.from_bytes(blob[off + 5:off + 7], "little"),
            "name": blob[off + 7:off + 3 + ln].decode("ascii", "replace"),
        })
        off += 3 + ln
    if off != term:
        return None

    return {
        "kind": "name_table",
        "offset": start,
        "length": (term + 2) - start,
        "note": "index is the address of a live state variable readable over "
                "USB; the numeric suffix in a name is its number of possible "
                "values - see FORMAT.md section 5c",
        "unknown_0x04": blob[start + 4],
        "records": records,
    }


def parse_pointer_table(blob: bytes, start: int, end: int, blob_len: int):
    """A count followed by 24-bit little-endian absolute flash addresses.

        <u8 count>  <u24 address>[count]
        <u16 count> <u24 address>[count]
        <u16 count> <00> <u24 address>[count]      (section 6)

    Accepted only when every address lands inside the config and the addresses
    strictly increase, and then only if the table either fills the span exactly
    or holds at least three entries. Those constraints matter: 0x02 is the most
    common byte in a config precisely because it is the high byte of these
    addresses, so a loose test finds pointer tables everywhere.

    These are the structures that have to be understood before a config can
    change length at all, which is why they get decoded early even where the
    data around them does not.
    """
    best = None
    for width in (1, 2):
        count = int.from_bytes(blob[start:start + width], "little")
        if count < 2:
            continue
        for head_len in range(width, 13):
            head = start + head_len
            if head + count * 3 > end:
                break
            addrs = [int.from_bytes(blob[head + 3 * k:head + 3 * k + 3], "little")
                     for k in range(count)]
            offs = [a - CONFIG_BASE for a in addrs]
            if not all(0 <= o < blob_len for o in offs):
                continue

            exact = head + count * 3 == end
            ascending = all(offs[i] < offs[i + 1] for i in range(len(offs) - 1))

            # An exact fit means the declared count, the header length and the
            # region boundary all agree, which is strong enough on its own.
            # A table that only occupies part of a section has none of that
            # corroboration, so it additionally has to ascend.
            if not exact and not (ascending and count >= 3):
                continue

            cand = (exact, count, width, head_len, offs,
                    head + count * 3 - start, ascending)
            if best is None or cand[:2] > best[:2]:
                best = cand
            if exact:
                break

    if best is None:
        return None
    exact, count, width, head_len, offs, length, ascending = best
    region = {
        "kind": "pointer_table",
        "offset": start,
        "length": length,
        "count_width": width,
        "header": blob[start + width:start + head_len].hex(),
        "note": "24-bit absolute flash addresses; stored here as blob offsets",
        "targets": offs,
    }
    if not ascending:
        region["unsorted"] = True
    return region


def find_record_starts(blob: bytes, section6_offset: int):
    """Offsets of the records indexed by section 6.

    Section 6 is `<u16 count> <00> <u24 address>[count]`, and the addresses run
    into the region below 0xF35B that the section table does not cover. That
    region is not unstructured: it is an array of records, and this is the
    index to it.
    """
    count = int.from_bytes(blob[section6_offset:section6_offset + 2], "little")
    head = section6_offset + 3
    starts = []
    for k in range(count):
        a = int.from_bytes(blob[head + 3 * k:head + 3 * k + 3], "little")
        o = a - CONFIG_BASE
        if not (0 <= o < len(blob)) or (starts and o <= starts[-1]):
            return []
        starts.append(o)
    return starts


def parse_record_header(blob: bytes, start: int, limit: int):
    """The header of one record in the array indexed by section 6.

        00
        u24        a back-reference, usually the previous record's start + 9
        u16 count
        u24[count] addresses

    Holds on all 114 records of the 525 config. The count is what earlier
    revisions of the docs read as a literal `01 00`: 108 records have exactly one
    address, so the field looked like a constant until the six with more turned
    up.

    That makes another 249 pointers explicit rather than copied.
    """
    if start >= limit or blob[start] != 0x00:
        return None
    back = int.from_bytes(blob[start + 1:start + 4], "little") - CONFIG_BASE
    count = int.from_bytes(blob[start + 4:start + 6], "little")
    if not (0 <= back < len(blob)) or not 1 <= count <= 64:
        return None
    head = start + 6
    if head + count * 3 > limit:
        return None
    targets = []
    for k in range(count):
        o = int.from_bytes(blob[head + 3 * k:head + 3 * k + 3], "little") - CONFIG_BASE
        if not (0 <= o < len(blob)):
            return None
        targets.append(o)
    return {
        "kind": "record_header",
        "offset": start,
        "length": 6 + count * 3,
        "back_reference": back,
        "targets": targets,
    }


REF_OPCODE = 0x16


def parse_block_header(blob: bytes, start: int, end: int):
    """Legacy weak recogniser for a byte shape once called a block header.

        16 <row> 03 00 <row*8> 00 <row*8> 60 08 <u24 address>

    Twelve bytes. Rooted screen control flow later proved this exact shape is
    two instructions: opcode 22 selects display row ``row`` and opcode 3 draws
    its 96x8 picture strip. All 1,080 global matches in the public 525 sample
    are rooted screen rows; none survives the stronger screen overlay. The weak
    recogniser remains only so an unrooted occurrence is preserved on other
    inputs rather than silently reinterpreted.

    The three bytes after `60 08` were written up as part of a fixed constant,
    `60 08 8B 2F 03`. They are not constant: they are an address, and the value
    only looked fixed because the records first read by hand happened to share
    it. All 1072 block headers in the 525 config carry a valid one, and all 1072
    point into section 17.
    """
    if start + 12 > end:
        return None
    b = blob
    row = b[start + 1]
    if (b[start] != 0x16 or b[start + 2] != 0x03 or b[start + 3] != 0x00
            or b[start + 4] != (row * 8) & 0xFF or b[start + 5] != 0x00
            or b[start + 6] != (row * 8) & 0xFF
            or b[start + 7] != 0x60 or b[start + 8] != 0x08):
        return None
    target = int.from_bytes(b[start + 9:start + 12], "little") - CONFIG_BASE
    if not (0 <= target < len(b)):
        return None
    return {
        "kind": "block_header",
        "offset": start,
        "length": 12,
        "matrix_row": row,
        "targets": [target],
    }


def find_references(blob: bytes, start: int, end: int):
    """`16 <u24 address>` inside a record body.

    Record bodies are eight blocks, one per matrix row, each opening with
    `16 <row> 03 00 <row*8> 00 <row*8> 60 08 8B 2F 03` and closing with `17`.
    The same `0x16` also appears followed by a real address, and those are
    pointers hiding in what was being treated as opaque hex.

    The two cannot be confused: a block header's next three bytes read as
    `<row> 03 00`, which is around 0x0300 and never a valid config address.

    124 of these turn up in the 525 config against roughly one expected by
    chance, and they come in pairs referring to the same target, so they are
    real. They matter because a length change would otherwise leave every one of
    them pointing at whatever moved into its place.
    """
    hits, i = [], start
    while i + 4 <= end:
        if blob[i] == REF_OPCODE:
            v = int.from_bytes(blob[i + 1:i + 4], "little") - CONFIG_BASE
            if 0 <= v < len(blob):
                hits.append((i, v))
                i += 4
                continue
        i += 1
    return hits


BITMAP_FORMAT = 0x02


def parse_bitmap(blob: bytes, start: int, end: int):
    """A monochrome bitmap for the remote's LCD.

        02          format
        u16 width
        u16 height
        bytes[width * height / 8]

    The 525's screen is 96 x 64 per the manual, and the header reads exactly
    that, with 768 bytes of pixels following it - one bit per pixel. Rendering
    them produces axis-aligned lines rather than noise, which is the other half
    of the argument.

    Section 17 is four of these back to back. Every one of the 1072 block headers
    in the config points at one of them, so a block is, among other things,
    choosing a screen to draw.
    """
    if start + 5 > end or blob[start] != BITMAP_FORMAT:
        return None
    w = int.from_bytes(blob[start + 1:start + 3], "little")
    h = int.from_bytes(blob[start + 3:start + 5], "little")
    # Calibrated to a real screen rather than to what the arithmetic allows.
    # A loose test - anything whose width times height divides by eight - finds
    # fifteen extra "bitmaps" in this config, most of them 256 pixels tall
    # because a 0x0100 fell where the height should be, and it eats four of the
    # five key tables on the way past. Another model with a different screen
    # will want these bounds widened, deliberately.
    if not (8 <= w <= 256 and 8 <= h <= 128) or w % 8 or h % 8:
        return None
    size = w * h // 8
    if start + 5 + size > end:
        return None
    return {
        "kind": "bitmap",
        "offset": start,
        "length": 5 + size,
        "format": f"0x{BITMAP_FORMAT:02X}",
        "width": w,
        "height": h,
        "note": "1 bit per pixel, row-major; the 525's LCD is 96x64",
        "pixels": _hex_lines(blob[start + 5:start + 5 + size]),
    }


def find_bitmaps(blob: bytes, start: int, end: int, allowed=None):
    """Bitmaps anywhere in a span, not only at its first byte.

    Section 17 opens with two bytes before the first one and closes with two
    after the last, so testing only the start of the span finds nothing.

    A minimum size keeps this from matching stray `02` bytes, and `allowed`
    restricts the search to offsets something actually points at. Both are
    needed: on an arch 8 config the size and dimension checks alone still find a
    56x64 "bitmap" that renders as speckle, and nothing in the file refers to it.
    """
    out, i, chain = [], start, -1
    while i < end:
        # a bitmap counts if something points at it, or if it sits immediately
        # after one that does - section 17 is an array, and its last entry is
        # unreferenced erased flash that still belongs to the array
        if allowed is not None and i not in allowed and i != chain:
            i += 1
            continue
        bm = parse_bitmap(blob, i, end)
        if bm and bm["length"] >= 5 + 256:
            out.append(bm)
            i += bm["length"]
            chain = i
        else:
            i += 1
    return out


def parse_action_list(blob: bytes, start: int, end: int):
    """A list of actions.

        <u8 count>  <u16 operand> <u8 opcode>  [count]

    Section 10 is an array of 487 addresses and nothing else, and every one of
    them lands on one of these. 482 of the 486 consecutive pairs are exactly
    `1 + 3 * count` apart, the other four leave a gap and none of them overlap,
    and the last list ends on the byte where the index itself begins. So the
    section is not an unexplained pointer array: it is the index to an array of
    action lists, and it tiles the region it covers.

    A key table's `target` is an index into it. That is what the original
    developer's `bindings: { button: executeActionList(n) }` looks like once it
    has been through the compiler, and it is the answer to what had been the
    most blocking open question here: pressing a key runs list number `target`.

    The instruction is the same three bytes section 8 is built from, which
    unifies two structures that had been described separately. Its opcodes are
    dominated by 0x7C, 0x7D, 0x7E and 0x7F - about three quarters of all 1043
    instructions in the 525 config - and what any of them mean is not known.

    Accepted only at an offset something points at. On its own the shape is far
    too weak to scan for: almost any byte can be read as a count.
    """
    n = blob[start]
    if not 1 <= n <= 64 or start + 1 + 3 * n > end:
        return None
    return {
        "kind": "action_list",
        "offset": start,
        "length": 1 + 3 * n,
        "instructions": [
            {"operand": int.from_bytes(blob[start + 1 + 3 * k:start + 3 + 3 * k],
                                       "little"),
             "opcode": f"0x{blob[start + 3 + 3 * k]:02X}"}
            for k in range(n)
        ],
    }


def find_action_lists(blob: bytes, regions):
    """Offsets of action lists, taken from whichever pointer table indexes them.

    Deliberately not hardcoded to section 10. A table qualifies if its targets
    behave like an index into a packed array of `1 + 3 * count` byte records:
    no two entries may overlap, and nearly all of them have to sit exactly
    where the previous one ended. Both halves matter - the overlap test is what
    stops an ordinary pointer table being read this way, and the adjacency test
    is what makes it positive evidence rather than the absence of a problem.
    """
    out = set()
    for r in regions:
        if r["kind"] != "pointer_table":
            continue
        t = sorted(r["targets"])
        if len(t) < 8:
            continue
        exact = 0
        for i in range(len(t) - 1):
            n = blob[t[i]]
            if not 1 <= n <= 64:
                exact = -1
                break
            finish = t[i] + 1 + 3 * n
            if finish > t[i + 1]:          # overlap: not an array of these
                exact = -1
                break
            if finish == t[i + 1]:
                exact += 1
        if exact >= 0.9 * (len(t) - 1):
            out.update(t)
    return frozenset(out)


ARCH9_SCREEN_FIXED = {1: 6, 2: 5, 3: 9, 4: 5, 16: 1, 17: 3, 22: 1, 23: 0}


def parse_arch9_screen_program(blob: bytes, start: int):
    """Read one linear arch-9 screen-program path.

    The parser mirrors the independently verified screen walker, but keeps the
    control-transfer targets so a caller can follow shared tails.  ``None`` is
    returned rather than guessing when any instruction is malformed.
    """
    result, offset, limit = [], start, len(blob)
    while 0 <= offset < limit:
        instruction = offset
        opcode = blob[offset]
        offset += 1
        if opcode == 0:
            result.append({"offset": instruction, "opcode": opcode,
                           "length": 1, "operands": b"", "targets": []})
            return result
        if opcode == 20:
            if offset + 3 > limit:
                return None
            operands = blob[offset:offset + 3]
            target = int.from_bytes(operands, "little") - CONFIG_BASE
            if not 0 <= target < limit:
                return None
            result.append({"offset": instruction, "opcode": opcode,
                           "length": 4, "operands": operands,
                           "targets": [target]})
            return result
        if opcode in ARCH9_SCREEN_FIXED:
            width = ARCH9_SCREEN_FIXED[opcode]
            if offset + width > limit:
                return None
            operands = blob[offset:offset + width]
            result.append({"offset": instruction, "opcode": opcode,
                           "length": 1 + width, "operands": operands,
                           "targets": []})
            offset += width
            continue
        if opcode == 5:
            if offset + 2 > limit:
                return None
            body = offset
            offset += 2
            while offset < limit and blob[offset] != 0:
                offset += 2 if blob[offset] & 0x80 else 1
            if offset >= limit:
                return None
            offset += 1
            result.append({"offset": instruction, "opcode": opcode,
                           "length": offset - instruction,
                           "operands": blob[body:offset], "targets": []})
            continue
        if opcode in (18, 19):
            width = 2 if opcode == 19 else 1
            body = offset
            if offset >= limit:
                return None
            offset += 1                 # state-variable index
            targets = []
            for entry_width in (width + 3, 2 * width + 3):
                if offset + width > limit:
                    return None
                count = int.from_bytes(blob[offset:offset + width], "little")
                offset += width
                if offset + count * entry_width > limit:
                    return None
                for index in range(count):
                    pointer = offset + index * entry_width + entry_width - 3
                    target = int.from_bytes(blob[pointer:pointer + 3], "little") - CONFIG_BASE
                    if not 0 <= target < limit:
                        return None
                    targets.append(target)
                offset += count * entry_width
            result.append({"offset": instruction, "opcode": opcode,
                           "length": offset - instruction,
                           "operands": blob[body:offset], "targets": targets})
            return result
        return None
    return None


def arch9_screen_roots(blob: bytes, section_pointers) -> list[int]:
    """Screen roots stated by slot 11 and every slot-6 mode page."""
    roots = []

    def offset_of(address):
        offset = address - CONFIG_BASE
        return offset if 0 <= offset < len(blob) else None

    if len(section_pointers) > 11 and section_pointers[11]:
        table = offset_of(section_pointers[11])
        if table is not None and table + 2 <= len(blob):
            count = int.from_bytes(blob[table:table + 2], "little")
            if table + 2 + 3 * count <= len(blob):
                for index in range(count):
                    root = offset_of(int.from_bytes(
                        blob[table + 2 + 3 * index:table + 5 + 3 * index], "little"))
                    if root is not None:
                        roots.append(root)

    if len(section_pointers) > 6 and section_pointers[6]:
        table = offset_of(section_pointers[6])
        if table is not None and table + 3 <= len(blob):
            count = int.from_bytes(blob[table:table + 3], "little")
            if count <= 4096 and table + 3 + 3 * count <= len(blob):
                for index in range(count):
                    mode = offset_of(int.from_bytes(
                        blob[table + 3 + 3 * index:table + 6 + 3 * index], "little"))
                    if mode is None or mode + 6 > len(blob):
                        continue
                    pages = int.from_bytes(blob[mode + 4:mode + 6], "little")
                    if pages > 256 or mode + 6 + 3 * pages > len(blob):
                        continue
                    for page_index in range(pages):
                        page = offset_of(int.from_bytes(
                            blob[mode + 6 + 3 * page_index:mode + 9 + 3 * page_index],
                            "little"))
                        if page is None or page + 6 > len(blob):
                            continue
                        root = offset_of(int.from_bytes(blob[page + 3:page + 6], "little"))
                        if root is not None:
                            roots.append(root)
    return sorted(set(roots))


def arch9_screen_text_regions(blob: bytes, section_pointers) -> list[dict]:
    """Every opcode-3/4 pointer and the terminated glyph strings opcode 4 names.

    These locations are derived from stated screen roots and control-flow
    successors.  That makes an opcode-4 pointer stronger evidence than scanning
    arbitrary bytes for ``04`` and is also what lets it supersede the older
    generic ``16 <u24>`` recogniser when a y-coordinate happens to be 0x16.
    """
    pending = arch9_screen_roots(blob, section_pointers)
    seen = set(pending)
    references = {}
    while pending:
        root = pending.pop()
        program = parse_arch9_screen_program(blob, root)
        if program is None:
            continue
        for instruction in program:
            if instruction["opcode"] == 3:
                operands = instruction["operands"]
                target = int.from_bytes(operands[6:9], "little") - CONFIG_BASE
                if 0 <= target < len(blob):
                    references[instruction["offset"]] = {
                        "kind": "screen_picture",
                        "offset": instruction["offset"],
                        "length": 10,
                        "coordinates": list(operands[:6]),
                        "targets": [target],
                    }
            if instruction["opcode"] == 4:
                operands = instruction["operands"]
                target = int.from_bytes(operands[2:5], "little") - CONFIG_BASE
                if 0 <= target < len(blob):
                    references[instruction["offset"]] = {
                        "kind": "screen_reference",
                        "offset": instruction["offset"],
                        "length": 6,
                        "opcode": "0x04",
                        "x": operands[0],
                        "y": operands[1],
                        "targets": [target],
                    }
            for target in instruction["targets"]:
                if target not in seen:
                    seen.add(target)
                    pending.append(target)

    # Two pointers can name the same terminated run of glyphs at different
    # starts, which makes the later one a suffix of the earlier string. Keep one
    # maximal region for the run: `symbolise` then writes the suffix pointer as
    # that region plus a delta, which is what the generic pointer representation
    # is for. The alternative is two overlapping writable regions, which is a
    # contradiction the compiler cannot resolve.
    # Only opcode 4 names text. Opcode 3 names a picture, and reading a
    # bitmap's `02 <u16 width> <u16 height>` header as glyphs gave four
    # three-byte "strings" of codes [2, 96] and left section 17's screens
    # opaque behind them.
    candidates = {}
    for reference in references.values():
        if reference["kind"] != "screen_reference":
            continue
        start = reference["targets"][0]
        end = start
        while end < len(blob) and blob[end] != 0:
            end += 2 if blob[end] & 0x80 else 1
        if end >= len(blob):
            continue
        candidates[start] = {
            "kind": "glyph_string",
            "offset": start,
            "length": end + 1 - start,
            "codes": list(blob[start:end]),
            "terminator": "0x00",
        }

    strings = {}
    ends = set()
    for start, string in sorted(candidates.items()):
        end = start + string["length"]
        if end in ends:
            continue
        strings[start] = string
        ends.add(end)

    known = sorted([*references.values(), *strings.values()],
                   key=lambda region: region["offset"])
    for left, right in zip(known, known[1:]):
        if left["offset"] + left["length"] > right["offset"]:
            raise ConfigError(
                f"overlapping screen regions at 0x{left['offset']:X} and "
                f"0x{right['offset']:X}")
    return known


def arch9_font_regions(blob: bytes, section_pointers) -> list[dict]:
    """The five arch-9 font-set pointer arrays, including significant NULLs."""
    if len(section_pointers) <= 7 or not section_pointers[7]:
        return []
    table = section_pointers[7] - CONFIG_BASE
    if not 0 <= table <= len(blob) - 2:
        return []
    count = int.from_bytes(blob[table:table + 2], "little")
    if count > 64 or table + 2 + 3 * count > len(blob):
        return []
    result = []
    for index in range(count):
        address = int.from_bytes(blob[table + 2 + 3 * index:table + 5 + 3 * index], "little")
        start = address - CONFIG_BASE
        if not 0 <= start <= len(blob) - 3:
            return []
        glyph_count = blob[start + 2]
        length = 3 + 3 * glyph_count
        if start + length > len(blob):
            return []
        targets = []
        for glyph in range(glyph_count):
            value = int.from_bytes(blob[start + 3 + 3 * glyph:start + 6 + 3 * glyph], "little")
            if value == 0:
                targets.append(None)
                continue
            offset = value - CONFIG_BASE
            if not 0 <= offset < len(blob):
                return []
            targets.append(offset)
        result.append({
            "kind": "font_set",
            "offset": start,
            "length": length,
            "height": blob[start],
            "first": blob[start + 1],
            "targets": targets,
        })
    return result


def arch9_mode_page_regions(blob: bytes, section_pointers) -> list[dict]:
    """Every arch-9 mode page's binding-list and screen-program pointers."""
    if len(section_pointers) <= 6 or not section_pointers[6]:
        return []
    table = section_pointers[6] - CONFIG_BASE
    if not 0 <= table <= len(blob) - 3:
        return []
    count = int.from_bytes(blob[table:table + 3], "little")
    if count > 4096 or table + 3 + 3 * count > len(blob):
        return []
    pages = {}
    for mode_index in range(count):
        entry_address = int.from_bytes(
            blob[table + 3 + 3 * mode_index:table + 6 + 3 * mode_index], "little")
        entry = entry_address - CONFIG_BASE
        if not 0 <= entry <= len(blob) - 6:
            return []
        page_count = int.from_bytes(blob[entry + 4:entry + 6], "little")
        if page_count > 256 or entry + 6 + 3 * page_count > len(blob):
            return []
        for page_index in range(page_count):
            page_address = int.from_bytes(
                blob[entry + 6 + 3 * page_index:entry + 9 + 3 * page_index], "little")
            page = page_address - CONFIG_BASE
            if not 0 <= page <= len(blob) - 6:
                return []
            targets = []
            for pointer in (page, page + 3):
                target = int.from_bytes(blob[pointer:pointer + 3], "little") - CONFIG_BASE
                if not 0 <= target < len(blob):
                    return []
                targets.append(target)
            pages[page] = {
                "kind": "mode_page",
                "offset": page,
                "length": 6,
                "targets": targets,
            }
    return list(pages.values())


def arch9_class5_ir_regions(blob: bytes, section_pointers) -> list[dict]:
    """Every pointer-bearing layer of the arch-9 class-5 IR graph.

    Danny Bloemendaal published and firmware-verified the class-5
    header/body/table/symbol-block layout in ``harmony-explorations``. This
    overlay applies that model to hconfig relocation: IR groups point to record
    class bytes, record headers point to bodies, bodies point to symbol tables,
    and symbol tables point to counted pulse blocks. Shared bodies, tables and
    symbols are emitted once.
    """
    if len(section_pointers) <= 5 or not section_pointers[5]:
        return []
    table = section_pointers[5] - CONFIG_BASE
    if not 0 <= table < len(blob):
        return []
    group_count = blob[table]
    if group_count == 0 or table + 1 + 3 * group_count > len(blob):
        return []

    regions = {}

    def remember(region: dict) -> None:
        offset = region["offset"]
        previous = regions.get(offset)
        if previous is not None and previous != region:
            raise ConfigError(f"conflicting class-5 regions at 0x{offset:X}")
        regions[offset] = region

    record_addresses = []
    for group_index in range(group_count):
        address = int.from_bytes(
            blob[table + 1 + 3 * group_index:table + 4 + 3 * group_index], "little")
        group = address - CONFIG_BASE
        if not 0 <= group <= len(blob) - 3 or blob[group] != 0:
            return []
        records = int.from_bytes(blob[group + 1:group + 3], "little")
        length = 3 + 3 * records
        if records > 4096 or group + length > len(blob):
            return []
        targets = []
        for command in range(records):
            target = int.from_bytes(
                blob[group + 3 + 3 * command:group + 6 + 3 * command], "little")
            offset = target - CONFIG_BASE
            if not 0 <= offset < len(blob):
                return []
            targets.append(offset)
            record_addresses.append(target)
        remember({
            "kind": "ir_group", "offset": group, "length": length,
            "targets": targets,
        })

    body_addresses = set()
    for record_address in sorted(set(record_addresses)):
        record = record_address - CONFIG_BASE
        start = record - 7
        if (start < 0 or record + 5 > len(blob) or blob[start] != 0
                or blob[record] != 5):
            return []
        back = int.from_bytes(blob[record + 1:record + 4], "little") - CONFIG_BASE
        groups = blob[start + 11]
        length = 12 + 9 * groups
        if not 1 <= groups <= 16 or start + length > len(blob) or back != start:
            return []
        targets = []
        for slot in range(3 * groups):
            address = int.from_bytes(
                blob[start + 12 + 3 * slot:start + 15 + 3 * slot], "little")
            if address == 0:
                targets.append(None)
                continue
            offset = address - CONFIG_BASE
            if not 0 <= offset <= len(blob) - 5:
                return []
            targets.append(offset)
            body_addresses.add(address)
        remember({
            "kind": "ir_record_header", "offset": start, "length": length,
            "period_ns": int.from_bytes(blob[start + 1:start + 4], "little"),
            "on_ns": int.from_bytes(blob[start + 4:start + 7], "little"),
            "ir_class": 5,
            "back_reference": back,
            "targets": targets,
        })

    table_addresses = set()
    for body_address in sorted(body_addresses):
        body = body_address - CONFIG_BASE
        table_address = int.from_bytes(blob[body:body + 3], "little")
        count = int.from_bytes(blob[body + 3:body + 5], "little")
        length = 5 + count
        if count > 8192 or body + length > len(blob):
            return []
        table_offset = table_address - CONFIG_BASE
        if not 0 <= table_offset < len(blob):
            return []
        table_addresses.add(table_address)
        remember({
            "kind": "ir_class5_body", "offset": body, "length": length,
            "targets": [table_offset],
            "indices": list(blob[body + 5:body + length]),
        })

    symbol_addresses = set()
    for table_address in sorted(table_addresses):
        symbol_table = table_address - CONFIG_BASE
        count = blob[symbol_table]
        length = 1 + 3 * count
        if count == 0 or symbol_table + length > len(blob):
            return []
        targets = []
        for index in range(count):
            address = int.from_bytes(
                blob[symbol_table + 1 + 3 * index:symbol_table + 4 + 3 * index], "little")
            offset = address - CONFIG_BASE
            if not 0 <= offset <= len(blob) - 4:
                return []
            targets.append(offset)
            symbol_addresses.add(address)
        remember({
            "kind": "ir_symbol_table", "offset": symbol_table,
            "length": length, "targets": targets,
        })

    for symbol_address in sorted(symbol_addresses):
        symbol = symbol_address - CONFIG_BASE
        count = int.from_bytes(blob[symbol:symbol + 2], "little")
        length = 4 + 2 * count
        if (count > 8192 or symbol + length > len(blob)
                or blob[symbol + length - 2:symbol + length] != b"\x00\x00"):
            return []
        remember({
            "kind": "ir_symbol_block", "offset": symbol, "length": length,
            "words": [int.from_bytes(blob[at:at + 2], "little")
                      for at in range(symbol + 2, symbol + length - 2, 2)],
        })

    known = sorted(regions.values(), key=lambda region: region["offset"])
    for left, right in zip(known, known[1:]):
        if left["offset"] + left["length"] > right["offset"]:
            raise ConfigError(
                f"overlapping class-5 regions at 0x{left['offset']:X} and "
                f"0x{right['offset']:X}")
    return known


def arch9_value_map_references(blob: bytes, section_pointers) -> list[dict]:
    """Pointers inside base-slot-14 value maps.

    The record layout and the fact that its targets are screen roots are Danny
    Bloemendaal's firmware/corpus-backed finding, published in
    ``harmony-explorations`` and pinned locally at commit ``a6516c7``.  Records
    can share tails, so this overlays only the u24 fields rather than claiming
    a second ownership of overlapping record bytes.
    """
    if len(section_pointers) <= 14 or not section_pointers[14]:
        return []
    table = section_pointers[14] - CONFIG_BASE
    if not 0 <= table < len(blob):
        return []
    record_count = blob[table]
    if table + 1 + 3 * record_count > len(blob):
        return []
    pointers = {}
    for record_index in range(record_count):
        address = int.from_bytes(
            blob[table + 1 + 3 * record_index:table + 4 + 3 * record_index], "little")
        record = address - CONFIG_BASE
        if not 0 <= record <= len(blob) - 2:
            return []
        entry_count = blob[record + 1]
        entries = record + 2
        spans = entries + 5 * entry_count
        if spans >= len(blob):
            return []
        for entry in range(entry_count):
            pointer = entries + 5 * entry + 2
            target = int.from_bytes(blob[pointer:pointer + 3], "little") - CONFIG_BASE
            if not 0 <= target < len(blob):
                return []
            pointers[pointer] = {
                "kind": "raw_pointer", "offset": pointer, "length": 3,
                "targets": [target],
            }
        span_count = blob[spans]
        if spans + 1 + 7 * span_count > len(blob):
            return []
        for span in range(span_count):
            pointer = spans + 1 + 7 * span + 4
            target = int.from_bytes(blob[pointer:pointer + 3], "little") - CONFIG_BASE
            if not 0 <= target < len(blob):
                return []
            pointers[pointer] = {
                "kind": "raw_pointer", "offset": pointer, "length": 3,
                "targets": [target],
            }
    return list(pointers.values())


RECOGNISERS = ("name_table", "pointer_table", "key_table", "bitmap")


def _refine_span(blob, parent, start, end, blob_len, skip=(), records=(),
                 bitmaps=None, actions=frozenset()):
    """Recognise structures inside one span, recursing into what is left over."""
    if start >= end:
        return []

    # record boundaries come from section 6 rather than from the bytes here, so
    # they are checked before anything that pattern-matches
    if records:
        inside = sorted(r for r in records if start < r < end)
        if inside:
            out = _refine_span(blob, parent, start, inside[0], blob_len, skip,
                               records, bitmaps, actions)
            for i, r in enumerate(inside):
                stop = inside[i + 1] if i + 1 < len(inside) else end
                out += _refine_span(blob, parent, r, stop, blob_len, skip,
                                    records, bitmaps, actions)
            return out
        if start in records:
            rh = parse_record_header(blob, start, end)
            if rh:
                return ([_annotate(rh, parent)]
                        + _split_references(blob, parent,
                                            start + rh["length"], end,
                                            bitmaps, actions))

    # action lists, like records, are known from an index rather than from
    # anything in their own bytes, so they are also settled before pattern
    # matching gets a chance to claim the same span
    if actions:
        inside = sorted(a for a in actions if start < a < end)
        if inside:
            out = _refine_span(blob, parent, start, inside[0], blob_len, skip,
                               records, bitmaps, actions)
            for i, a in enumerate(inside):
                stop = inside[i + 1] if i + 1 < len(inside) else end
                out += _refine_span(blob, parent, a, stop, blob_len, skip,
                                    records, bitmaps, actions)
            return out
        if start in actions:
            al = parse_action_list(blob, start, end)
            if al:
                return ([_annotate(al, parent)]
                        + _refine_span(blob, parent, start + al["length"], end,
                                       blob_len, skip, records, bitmaps,
                                       actions))

    if "name_table" not in skip:
        nt = parse_name_table(blob, start, end)
        if nt:
            return ([_annotate(nt, parent)]
                    + _refine_span(blob, parent, start + nt["length"], end,
                                   blob_len, bitmaps=bitmaps, actions=actions))

    if "bitmap" not in skip:
        found = find_bitmaps(blob, start, end, bitmaps)
        if found:
            out, cursor = [], start
            for bm in found:
                if bm["offset"] > cursor:
                    out += _refine_span(blob, parent, cursor, bm["offset"],
                                        blob_len, skip=RECOGNISERS,
                                        bitmaps=bitmaps, actions=actions)
                out.append(_annotate(bm, parent))
                cursor = bm["offset"] + bm["length"]
            if cursor < end:
                out += _refine_span(blob, parent, cursor, end, blob_len,
                                    skip=RECOGNISERS, bitmaps=bitmaps,
                                    actions=actions)
            return out

    if "pointer_table" not in skip:
        pt = parse_pointer_table(blob, start, end, blob_len)
        if pt:
            return ([_annotate(pt, parent)]
                    + _refine_span(blob, parent, start + pt["length"], end,
                                   blob_len, skip=("pointer_table",),
                                   bitmaps=bitmaps, actions=actions))

    if "key_table" not in skip:
        found = find_key_tables(blob, start, end)
        if found:
            out, cursor = [], start
            for off, count, stride in found:
                if off > cursor:
                    out += _refine_span(blob, parent, cursor, off, blob_len,
                                        skip=RECOGNISERS, bitmaps=bitmaps,
                                        actions=actions)
                out.append(_key_table_region(blob, off, count, parent, stride))
                cursor = off + count * stride
            out += _refine_span(blob, parent, cursor, end, blob_len,
                                skip=RECOGNISERS, bitmaps=bitmaps,
                                actions=actions)
            return out

    return [_slice(parent, blob, start, end)]


def parse_record_trailer(blob: bytes, start: int, end: int):
    """The last seven bytes of a record.

        00 <u24 into section 8> <u24 back into this record>

    The second address lands on the record's own start + 11 where it has been
    checked. The first points into section 8, the suspected bytecode, which is
    the evidence that each record carries its own program.

    113 of the 114 records end this way.
    """
    if end - start < 7 or blob[end - 7] != 0x00:
        return None
    a = int.from_bytes(blob[end - 6:end - 3], "little") - CONFIG_BASE
    b = int.from_bytes(blob[end - 3:end], "little") - CONFIG_BASE
    if not (0 <= a < len(blob)) or not (0 <= b < len(blob)):
        return None
    return {
        "kind": "record_trailer",
        "offset": end - 7,
        "length": 7,
        "note": "first target is in section 8, the suspected bytecode",
        "targets": [a, b],
    }


def _split_references(blob, parent, start, end, bitmaps=None,
                      actions=frozenset()):
    """A record body: opaque hex, with everything recognisable pulled out."""
    if start >= end:
        return []

    trailer = parse_record_trailer(blob, start, end)
    if trailer:
        return (_split_references(blob, parent, start, end - 7, bitmaps, actions)
                + [_annotate(trailer, parent)])

    found, i = [], start
    while i < end:
        bh = parse_block_header(blob, i, end)
        if bh:
            found.append(bh)
            i += 12
            continue
        if blob[i] == REF_OPCODE and i + 4 <= end:
            v = int.from_bytes(blob[i + 1:i + 4], "little") - CONFIG_BASE
            if 0 <= v < len(blob):
                found.append({
                    "kind": "reference",
                    "offset": i,
                    "length": 4,
                    "opcode": f"0x{REF_OPCODE:02X}",
                    "targets": [v],
                })
                i += 4
                continue
        i += 1

    out, cursor = [], start
    for region in found:
        off = region["offset"]
        if off > cursor:
            out += _refine_span(blob, parent, cursor, off, len(blob),
                                skip=("name_table", "pointer_table"),
                                bitmaps=bitmaps, actions=actions)
        out.append(region)
        cursor = off + region["length"]
    if cursor < end:
        out += _refine_span(blob, parent, cursor, end, len(blob),
                            skip=("name_table", "pointer_table"),
                            bitmaps=bitmaps, actions=actions)
    return out


def _annotate(region, parent):
    if "section" in parent:
        region["section"] = parent["section"]
    return region


def _refine(blob: bytes, regions, records=(), bitmaps=None,
            actions=frozenset()):
    """Split opaque and section regions wherever a recogniser identifies something."""
    out = []
    for r in regions:
        if r["kind"] not in ("opaque", "section"):
            out.append(r)
            continue
        out += _refine_span(blob, r, r["offset"], r["offset"] + r["length"],
                            len(blob), records=records, bitmaps=bitmaps,
                            actions=actions)
    return out


def _slice(parent, blob, a, b):
    """A leftover piece of a split region, carrying its parent's annotations."""
    piece = {
        "kind": parent["kind"],
        "offset": a,
        "length": b - a,
        "data": _hex_lines(blob[a:b]),
    }
    if "section" in parent:
        piece["section"] = parent["section"]
        piece["part"] = "remainder"
    if "note" in parent:
        piece["note"] = parent["note"]
    return piece


def _overlay_known_regions(blob: bytes, regions: list[dict], known: list[dict]):
    """Overlay exact semantic spans on an already tiled region list.

    Screen references are derived from control flow after the ordinary refine
    passes.  A reference can cross a boundary introduced by a weaker recogniser
    (notably an opcode-4 y-coordinate of ``0x16`` followed by its pointer), so
    this overlay is allowed to replace whole recognised regions and to split
    data-backed regions.  It refuses to cut only part of any recognised region.
    """
    if not known:
        return regions

    result = []
    index = 0
    cursor = 0

    def raw_piece(region, start, stop):
        parent = {"kind": "opaque"}
        if "section" in region:
            parent["section"] = region["section"]
        parent["note"] = "raw remainder after a stronger semantic overlay"
        return _slice(parent, blob, start, stop)

    def advance(to: int, keep: bool):
        nonlocal index, cursor
        while cursor < to:
            while index < len(regions) and (
                    regions[index]["offset"] + regions[index]["length"] <= cursor):
                index += 1
            if index >= len(regions):
                raise ConfigError("semantic overlay ran past the region tiling")
            region = regions[index]
            start = region["offset"]
            stop = start + region["length"]
            if not start <= cursor < stop:
                raise ConfigError(f"gap in region tiling at 0x{cursor:X}")
            finish = min(to, stop)
            if keep:
                if cursor == start and finish == stop:
                    result.append(region)
                elif "data" in region:
                    result.append(_slice(region, blob, cursor, finish))
                else:
                    # A control-flow-derived screen span is stronger than the
                    # generic byte-pattern recognisers. Its boundary can expose
                    # that only part of an older `reference` was real. Preserve
                    # the uncovered bytes verbatim rather than retaining a
                    # semantic claim whose framing has been disproved.
                    result.append(raw_piece(region, cursor, finish))
            cursor = finish

    for semantic in known:
        start = semantic["offset"]
        stop = start + semantic["length"]
        if start < cursor:
            raise ConfigError(f"overlapping semantic overlay at 0x{start:X}")
        advance(start, keep=True)

        touched = [r for r in regions
                   if r["offset"] < stop and start < r["offset"] + r["length"]]
        sections = {r["section"] for r in touched if "section" in r}
        if len(sections) == 1:
            semantic["section"] = sections.pop()
        advance(stop, keep=False)
        result.append(semantic)

    advance(len(blob), keep=True)
    return result


# --------------------------------------------------------------------------
# decompile

def decompile(raw: bytes, filename=None) -> dict:
    xml, sep, blob = split_container(raw)

    magic = bytes(blob[:4])
    arch = ARCHITECTURES.get(magic)
    if arch is None:
        raise ConfigError(
            f"unrecognised magic {magic!r}; known: "
            + ", ".join(m.decode() for m in ARCHITECTURES))
    if blob[-4:] != arch["end"]:
        raise ConfigError(f"expected {arch['end'].decode()} at the end, "
                          f"found {blob[-4:]!r}")

    marker = blob.find(arch["marker"])
    if marker == -1:
        raise ConfigError(f"no {arch['marker'].decode()} marker in the header")
    header_len = marker + len(arch["marker"])

    end_address = _u32(blob, 4)
    unknown_08 = _u32(blob, 8)

    # Read the whole pointer table, then drop the zeros that pad it out to the
    # marker. Zeros *inside* the table are not padding: they mean the subsystem
    # is not present, and arch 8 has one. Stopping at the first zero, which is
    # what this used to do, silently truncates an arch 8 table to eight entries.
    raw_ptrs, off = [], PTR_TABLE_OFF
    while off + 4 <= marker:
        raw_ptrs.append(_u32(blob, off))
        off += 4
    while raw_ptrs and raw_ptrs[-1] == 0:
        raw_ptrs.pop()
    ptrs = [p if p else None for p in raw_ptrs]
    padding = blob[PTR_TABLE_OFF + 4 * len(ptrs):marker]

    # the addresses tile the region from the first pointer to the end marker
    end_off = len(blob) - 4
    present = [(i, p - CONFIG_BASE) for i, p in enumerate(ptrs) if p is not None]
    for n, (i, o) in enumerate(present):
        if not (0 <= o < len(blob)):
            raise ConfigError(f"section {i} address 0x{ptrs[i]:X} is out of range")
        nxt = present[n + 1][1] if n + 1 < len(present) else end_off
        if nxt < o:
            raise ConfigError(f"section {i} is not followed by a later address")

    regions = [{
        "kind": "blob_header",
        "offset": 0,
        "length": header_len,
        "architecture": arch["name"],
        "magic": magic.decode(),
        "end_address": f"0x{end_address:06X}",
        "unknown_0x08": f"0x{unknown_08:08X}",
        "section_count": len(ptrs),
        "absent_sections": [i for i, x in enumerate(ptrs) if x is None],
        "padding": padding.hex(),
        "marker": arch["marker"].decode(),
        "end_marker": arch["end"].decode(),
    }]

    first_section = present[0][1]
    if first_section > header_len:
        regions.append({
            "kind": "opaque",
            "note": "not covered by the section table; on arch 9 this holds the "
                    "record array indexed by section 6",
            "offset": header_len,
            "length": first_section - header_len,
            "data": _hex_lines(blob[header_len:first_section]),
        })

    for n, (i, a) in enumerate(present):
        b = present[n + 1][1] if n + 1 < len(present) else end_off
        regions.append({
            "kind": "section",
            "section": i,
            "offset": a,
            "length": b - a,
            "data": _hex_lines(blob[a:b]),
        })

    regions.append({
        "kind": "blob_footer",
        "offset": end_off,
        "length": 4,
        "magic": arch["end"].decode(),
    })

    # section 6 indexes the record array; find it before refining so the low
    # region can be split on record boundaries rather than guessed at
    records = ()
    if ptrs and len(ptrs) > 6 and ptrs[6]:
        records = frozenset(find_record_starts(blob, ptrs[6] - CONFIG_BASE))

    # Two passes. The first finds the structures that can be recognised on
    # their own; the second is told what those point at, and only accepts a
    # bitmap or an action list at an offset something actually refers to.
    #
    # Both restrictions are load-bearing rather than tidiness. The bitmap
    # dimension checks alone still pass a 56x64 region of speckle in the arch 8
    # samples that nothing in the file refers to, and an action list is a count
    # byte followed by three-byte instructions, which almost any run of bytes
    # can be read as.
    # What refers to a bitmap used to be the block_header recogniser, which was
    # withdrawn when the rooted screen walk superseded it (FORMAT 4f). Nothing
    # replaced it, so the gate has been closed on an empty set ever since and
    # section 17's four screens came out opaque. The rooted walk knows the same
    # addresses and knows them better: opcode 3 carries the picture it draws.
    arch9_screens = (arch9_screen_text_regions(blob, ptrs)
                     if magic == b"AHCM" and ptrs else [])
    first = _refine(blob, regions, records, bitmaps=frozenset())
    targets = frozenset(x for r in first if r["kind"] == "block_header"
                        for x in r["targets"])
    targets |= frozenset(x for r in arch9_screens
                         if r["kind"] == "screen_picture" for x in r["targets"])
    actions = find_action_lists(blob, first)
    regions = (_refine(blob, regions, records, bitmaps=targets, actions=actions)
               if targets or actions else first)

    # The arch-9 screen parser is rooted in slot 11 and the slot-6 mode pages,
    # not in byte-pattern guesses. Pull opcode-4 references and their strings
    # out last so they take precedence over weaker recognisers they may cross.
    if magic == b"AHCM":
        known = [*arch9_class5_ir_regions(blob, ptrs),
                 *arch9_font_regions(blob, ptrs),
                 *arch9_mode_page_regions(blob, ptrs),
                 *arch9_value_map_references(blob, ptrs),
                 *arch9_screens]
        regions = _overlay_known_regions(
            blob, regions, sorted(known, key=lambda region: region["offset"]))

    doc = {
        "harmony_config_version": FORMAT_VERSION,
        "source": {
            "filename": filename,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "container": None,
        "blob": {
            "size": len(blob),
            "config_base": f"0x{CONFIG_BASE:X}",
            "regions": regions,
        },
    }

    if xml is not None:
        try:
            text = xml.decode("ascii")
            ok = text.encode("ascii") == xml
        except UnicodeDecodeError:
            ok = False
        doc["container"] = {
            "kind": "ezhex",
            "separator": sep.hex(),
            "declared_binary_size": int(_tag(xml, "BINARYDATASIZE") or -1),
            "declared_checksum": int(_tag(xml, "CHECKSUM") or -1),
            "xml_header": text if ok else None,
            "xml_header_hex": None if ok else _hex_lines(xml),
        }

    return doc


# --------------------------------------------------------------------------
# compile

def emit_region(r: dict, resolve=None) -> bytes:
    """Turn one region back into bytes.

    `resolve` maps a pointer to a blob offset. Absolute pointers pass straight
    through; symbolic ones need the layout, which is why compile_blob works out
    every region's position before emitting any of them.
    """
    if resolve is None:
        def resolve(t):
            if isinstance(t, int):
                return t
            raise ConfigError("symbolic pointers need a resolver")

    kind = r["kind"]

    if kind in ("opaque", "section"):
        return _unhex(r["data"])

    if kind == "name_table":
        body = bytearray()
        for rec in r["records"]:
            name = rec["name"].encode("ascii")
            body += bytes([NAME_REC])
            body += (len(name) + 4).to_bytes(2, "little")
            body += rec["parent"].to_bytes(2, "little")
            body += rec["index"].to_bytes(2, "little")
            body += name
        declared = 5 + len(body)
        return (NAME_MAGIC + declared.to_bytes(2, "little")
                + bytes([r["unknown_0x04"]]) + bytes(body) + NAME_END)

    if kind == "reference":
        return (bytes([int(r["opcode"], 16)])
                + (resolve(r["targets"][0]) + CONFIG_BASE).to_bytes(3, "little"))

    if kind == "screen_reference":
        return (bytes([int(r["opcode"], 16), r["x"], r["y"]])
                + (resolve(r["targets"][0]) + CONFIG_BASE).to_bytes(3, "little"))

    if kind == "glyph_string":
        return bytes(r["codes"]) + bytes([int(r["terminator"], 16)])

    if kind == "bitmap":
        return (bytes([int(r["format"], 16)])
                + r["width"].to_bytes(2, "little")
                + r["height"].to_bytes(2, "little")
                + _unhex(r["pixels"]))

    if kind == "action_list":
        out = bytearray([len(r["instructions"])])
        for ins in r["instructions"]:
            out += ins["operand"].to_bytes(2, "little")
            out += bytes([int(ins["opcode"], 16)])
        return bytes(out)

    if kind == "record_trailer":
        out = bytearray(bytes([0]))
        for x in r["targets"]:
            out += (resolve(x) + CONFIG_BASE).to_bytes(3, "little")
        return bytes(out)

    if kind == "block_header":
        row = r["matrix_row"]
        return (bytes([0x16, row, 0x03, 0x00, (row * 8) & 0xFF, 0x00,
                       (row * 8) & 0xFF, 0x60, 0x08])
                + (resolve(r["targets"][0]) + CONFIG_BASE).to_bytes(3, "little"))

    if kind == "record_header":
        out = bytearray(b"\x00")
        out += (resolve(r["back_reference"]) + CONFIG_BASE).to_bytes(3, "little")
        out += len(r["targets"]).to_bytes(2, "little")
        for t in r["targets"]:
            out += (resolve(t) + CONFIG_BASE).to_bytes(3, "little")
        return bytes(out)

    if kind == "ir_group":
        targets = r["targets"]
        if len(targets) > 0xFFFF:
            raise ConfigError("IR group record count does not fit u16")
        out = bytearray(b"\x00")
        out += len(targets).to_bytes(2, "little")
        for target in targets:
            out += (resolve(target) + CONFIG_BASE).to_bytes(3, "little")
        return bytes(out)

    if kind == "ir_record_header":
        targets = r["targets"]
        if (not targets or len(targets) % 3 or len(targets) // 3 > 16
                or int(r["ir_class"]) != 5):
            raise ConfigError("invalid arch-9 class-5 record header")
        out = bytearray(b"\x00")
        out += int(r["period_ns"]).to_bytes(3, "little")
        out += int(r["on_ns"]).to_bytes(3, "little")
        out += bytes((5,))
        out += (resolve(r["back_reference"]) + CONFIG_BASE).to_bytes(3, "little")
        out += bytes((len(targets) // 3,))
        for target in targets:
            address = 0 if target is None else resolve(target) + CONFIG_BASE
            out += address.to_bytes(3, "little")
        return bytes(out)

    if kind == "ir_class5_body":
        if len(r["targets"]) != 1 or len(r["indices"]) > 0xFFFF:
            raise ConfigError("invalid class-5 body")
        return ((resolve(r["targets"][0]) + CONFIG_BASE).to_bytes(3, "little")
                + len(r["indices"]).to_bytes(2, "little")
                + bytes(r["indices"]))

    if kind == "ir_symbol_table":
        if not r["targets"] or len(r["targets"]) > 0xFF:
            raise ConfigError("invalid class-5 symbol table")
        out = bytearray((len(r["targets"]),))
        for target in r["targets"]:
            out += (resolve(target) + CONFIG_BASE).to_bytes(3, "little")
        return bytes(out)

    if kind == "ir_symbol_block":
        if len(r["words"]) > 8192 or any(not 0 < word <= 0xFFFF for word in r["words"]):
            raise ConfigError("invalid class-5 symbol block")
        out = bytearray(len(r["words"]).to_bytes(2, "little"))
        for word in r["words"]:
            out += int(word).to_bytes(2, "little")
        out += b"\x00\x00"
        return bytes(out)

    if kind == "tagged_list":
        entries = r["entries"]
        if not entries or len(entries) > 0xFF:
            raise ConfigError("tagged list must contain 1..255 entries")
        wide = r.get("wide", any("flags" in entry for entry in entries))
        out = bytearray((0, len(entries))) if wide else bytearray((len(entries),))
        for entry in entries:
            tag, operand = entry["tag"], entry["operand"]
            opcode = entry["opcode"]
            if not 0 <= tag <= 0xFF or not 0 <= operand <= 0xFFFF or not 0 <= opcode <= 0xFF:
                raise ConfigError("tagged-list field is outside its stored width")
            if wide:
                flags = entry.get("flags", 0)
                if not 0 <= flags <= 0xFF:
                    raise ConfigError("tagged-list flags do not fit u8")
                out += bytes((flags, tag))
            else:
                out += bytes((tag,))
            out += operand.to_bytes(2, "little") + bytes((opcode,))
        return bytes(out)

    if kind == "mode_page":
        if len(r["targets"]) != 2:
            raise ConfigError("an arch-9 mode page needs list and program pointers")
        out = bytearray()
        for target in r["targets"]:
            out += (resolve(target) + CONFIG_BASE).to_bytes(3, "little")
        return bytes(out)

    if kind == "screen_picture":
        coordinates = bytes(r["coordinates"])
        if len(coordinates) != 6:
            raise ConfigError("screen-picture coordinates must be six bytes")
        return (bytes((3,)) + coordinates
                + (resolve(r["targets"][0]) + CONFIG_BASE).to_bytes(3, "little"))

    if kind == "font_set":
        targets = r["targets"]
        if len(targets) > 0xFF:
            raise ConfigError("font-set glyph count does not fit u8")
        out = bytearray((r["height"], r["first"], len(targets)))
        for target in targets:
            address = 0 if target is None else resolve(target) + CONFIG_BASE
            out += address.to_bytes(3, "little")
        return bytes(out)

    if kind == "raw_pointer":
        if len(r["targets"]) != 1:
            raise ConfigError("raw pointer must carry exactly one target")
        return (resolve(r["targets"][0]) + CONFIG_BASE).to_bytes(3, "little")

    if kind == "pointer_table":
        targets = r["targets"]
        out = bytearray()
        out += len(targets).to_bytes(r["count_width"], "little")
        out += bytes.fromhex(r.get("header", r.get("padding", "")))
        for t in targets:
            out += (resolve(t) + CONFIG_BASE).to_bytes(3, "little")
        return bytes(out)

    if kind == "key_table":
        term = int(r["terminator"], 16)
        out = bytearray()
        for e in r["entries"]:
            code = int(e["code"], 16) if isinstance(e["code"], str) else e["code"]
            if not 0 <= code <= 0xFF:
                raise ConfigError(f"key code {e['code']} does not fit in a byte")
            if not 0 <= e["target"] <= 0xFFFF:
                raise ConfigError(f"target {e['target']} does not fit in u16")
            if "flag" in e:
                out += bytes([int(e["flag"], 16)])
            out += bytes([code])
            out += e["target"].to_bytes(2, "little")
            out += bytes([term])
        return bytes(out)

    raise ConfigError(f"unknown region kind {kind!r}")


def compile_blob(doc: dict) -> bytes:
    """Rebuild the binary blob, recomputing every pointer we understand."""
    regions = doc["blob"]["regions"]

    header = next((r for r in regions if r["kind"] == "blob_header"), None)
    footer = next((r for r in regions if r["kind"] == "blob_footer"), None)
    if header is None or footer is None:
        raise ConfigError("regions must include a blob_header and a blob_footer")

    # work out where every region lands before emitting any of them, so
    # symbolic pointers can be resolved against the new layout
    resolve, _ = _resolver(regions)

    body, addresses, cursor = [], {}, header["length"]
    for r in regions:
        kind = r["kind"]
        if kind in ("blob_header", "blob_footer"):
            continue

        # a section may have been split into several regions; its address is
        # where the first of them lands
        if "section" in r and r["section"] not in addresses:
            addresses[r["section"]] = cursor + CONFIG_BASE

        data = emit_region(r, resolve)
        body.append(data)
        cursor += len(data)

    absent = set(header.get("absent_sections", []))
    count = header.get("section_count", len(addresses) + len(absent))
    expected = set(range(count)) - absent
    if set(addresses) != expected:
        missing = sorted(expected - set(addresses))
        extra = sorted(set(addresses) - expected)
        raise ConfigError(
            f"section indices do not match the header: missing {missing}, "
            f"unexpected {extra}")

    end_address = cursor + CONFIG_BASE
    padding = bytes.fromhex(header["padding"])
    declared = int(header["unknown_0x08"], 16)

    out = bytearray()
    out += header["magic"].encode("ascii")
    out += _p32(end_address)
    out += _p32(declared)
    for i in range(count):
        # an absent section keeps its null; it is not a gap to be closed up
        out += _p32(0 if i in absent else addresses[i])
    out += padding
    out += header["marker"].encode("ascii")

    if len(out) != header["length"]:
        raise ConfigError(
            f"rebuilt header is {len(out)} B, the source says "
            f"{header['length']} B - the pointer table or padding changed size")

    for chunk in body:
        out += chunk
    out += header.get("end_marker", END_MAGIC.decode()).encode("ascii")
    # The two bytes immediately before the end marker are checked by the
    # remote's boot validator. They used to pass through as opaque section-17
    # data, which made edited configs internally inconsistent even when the
    # EZHex XML checksum was refreshed.
    checksum_at = len(out) - TRAILER_CHECKSUM_OFFSET
    out[checksum_at:checksum_at + 2] = trailer_checksum(out).to_bytes(2, "little")
    return bytes(out)


def compile_config(doc: dict) -> bytes:
    """Rebuild the whole file, updating the container's declarations."""
    blob = compile_blob(doc)
    c = doc.get("container")
    if not c:
        return blob

    if c.get("xml_header") is not None:
        xml = c["xml_header"].encode("ascii")
    else:
        xml = _unhex(c["xml_header_hex"])

    # keep the declarations honest even if the blob changed
    xml = _set_tag(xml, "BINARYDATASIZE", len(blob))
    xml = _set_tag(xml, "CHECKSUM", blob_checksum(blob))

    return xml + bytes.fromhex(c["separator"]) + blob


# --------------------------------------------------------------------------

def symbolise(doc: dict) -> dict:
    """Turn every pointer from an absolute offset into `region + delta`.

    An offset only stays correct while nothing before it moves, which is why
    lengths currently have to be preserved. A pointer that says "12 bytes into
    record 42" instead stays correct wherever record 42 ends up, so the compiler
    can recompute it after a change in length.

    Pointers are resolved against the region that contains them, not the nearest
    labelled thing: 705 of the 934 in the 525 config land inside a region rather
    than on its first byte, mostly in record bodies that are still opaque.
    """
    regions = doc["blob"]["regions"]
    for i, r in enumerate(regions):
        r.setdefault("id", f"r{i:04d}")

    starts = sorted((r["offset"], r["id"]) for r in regions)
    offsets = [s for s, _ in starts]

    def to_symbol(off):
        import bisect
        i = bisect.bisect_right(offsets, off) - 1
        if i < 0:
            return off
        base, rid = starts[i]
        return {"to": rid, "delta": off - base} if off != base else {"to": rid}

    for r in regions:
        if "targets" in r:
            r["targets"] = [to_symbol(t) if isinstance(t, int) else t
                            for t in r["targets"]]
        if "back_reference" in r and isinstance(r["back_reference"], int):
            r["back_reference"] = to_symbol(r["back_reference"])

    doc["blob"]["pointers_symbolic"] = True
    return doc


def _resolver(regions):
    """Map region ids to their final offsets, then resolve a pointer.

    Two passes are possible because a pointer is always three bytes whatever it
    points at, so every region's length is known before any address is.
    """
    offsets, cursor = {}, 0
    for r in regions:
        if "id" in r:
            offsets[r["id"]] = cursor
        cursor += region_length(r)

    def resolve(t):
        if isinstance(t, int):
            return t
        if t["to"] not in offsets:
            raise ConfigError(f"pointer to unknown region {t['to']!r}")
        return offsets[t["to"]] + t.get("delta", 0)

    return resolve, cursor


def region_length(r: dict) -> int:
    """A region's size in bytes, without needing any pointer resolved."""
    kind = r["kind"]
    if kind in ("opaque", "section"):
        return sum(len(line) for line in r["data"]) // 2
    if kind == "blob_header":
        return r["length"]
    if kind == "blob_footer":
        return 4
    if kind == "name_table":
        return 7 + sum(7 + len(rec["name"]) for rec in r["records"])
    if kind == "key_table":
        return r.get("entry_stride", 4) * len(r["entries"])
    if kind == "pointer_table":
        return (r["count_width"] + len(bytes.fromhex(r.get("header", "")))
                + 3 * len(r["targets"]))
    if kind == "record_header":
        return 6 + 3 * len(r["targets"])
    if kind == "ir_group":
        return 3 + 3 * len(r["targets"])
    if kind == "ir_record_header":
        return 12 + 3 * len(r["targets"])
    if kind == "ir_class5_body":
        return 5 + len(r["indices"])
    if kind == "ir_symbol_table":
        return 1 + 3 * len(r["targets"])
    if kind == "ir_symbol_block":
        return 4 + 2 * len(r["words"])
    if kind == "tagged_list":
        wide = r.get("wide", any("flags" in entry for entry in r["entries"]))
        return (2 if wide else 1) + (5 if wide else 4) * len(r["entries"])
    if kind == "mode_page":
        return 6
    if kind == "screen_picture":
        return 10
    if kind == "font_set":
        return 3 + 3 * len(r["targets"])
    if kind == "raw_pointer":
        return 3
    if kind == "reference":
        return 4
    if kind == "screen_reference":
        return 6
    if kind == "glyph_string":
        return len(r["codes"]) + 1
    if kind == "block_header":
        return 12
    if kind == "record_trailer":
        return 7
    if kind == "action_list":
        return 1 + 3 * len(r["instructions"])
    if kind == "bitmap":
        return 5 + r["width"] * r["height"] // 8
    raise ConfigError(f"unknown region kind {kind!r}")


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(doc: dict, path):
    Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def roundtrip(raw: bytes, filename=None):
    """Decompile then recompile. Returns (ok, rebuilt, doc)."""
    doc = decompile(raw, filename)
    rebuilt = compile_config(doc)
    return rebuilt == raw, rebuilt, doc


def first_difference(a: bytes, b: bytes):
    """Offset of the first differing byte, or None."""
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))
