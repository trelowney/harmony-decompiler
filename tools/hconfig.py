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

Pointers are recomputed on compile rather than copied, so section contents can
change length and the section table still comes out correct. Pointers *inside*
opaque regions obviously cannot be, which is why lengths must currently stay
fixed - see docs/OPEN-QUESTIONS.md.

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
    spans, i = [], start
    while i + 4 <= end:
        if blob[i + 3] != KEY_TERM:
            i += 1
            continue
        run_start, count = i, 0
        while i + 4 <= end and blob[i + 3] == KEY_TERM:
            count += 1
            i += 4
        if count < KEY_MIN_ENTRIES:
            continue
        codes = [blob[run_start + k * 4] for k in range(count)]
        if len(set(codes)) / count < KEY_MIN_UNIQUE:
            continue
        spans.append((run_start, count))
    return spans


def _key_table_region(blob, off, count, parent=None):
    entries = []
    for k in range(count):
        p = off + k * 4
        entries.append({
            "code": f"0x{blob[p]:02X}",
            "target": int.from_bytes(blob[p + 1:p + 3], "little"),
        })
    region = {
        "kind": "key_table",
        "offset": off,
        "length": count * 4,
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
        for pad in (0, 1):
            if pad and width == 1:
                continue
            head = start + width + pad
            count = int.from_bytes(blob[start:start + width], "little")
            if count < 2 or head + count * 3 > end:
                continue
            if pad and blob[start + width] != 0:
                continue
            addrs = [int.from_bytes(blob[head + 3 * k:head + 3 * k + 3], "little")
                     for k in range(count)]
            offs = [a - CONFIG_BASE for a in addrs]
            if not all(0 <= o < blob_len for o in offs):
                continue
            if any(offs[i] >= offs[i + 1] for i in range(len(offs) - 1)):
                continue
            exact = head + count * 3 == end
            if not exact and count < 3:
                continue
            cand = (exact, count, width, pad, offs, head + count * 3 - start)
            if best is None or cand[:2] > best[:2]:
                best = cand

    if best is None:
        return None
    exact, count, width, pad, offs, length = best
    return {
        "kind": "pointer_table",
        "offset": start,
        "length": length,
        "count_width": width,
        "padding": "00" * pad,
        "note": "24-bit absolute flash addresses; stored here as blob offsets",
        "targets": offs,
    }


RECOGNISERS = ("name_table", "pointer_table", "key_table")


def _refine_span(blob, parent, start, end, blob_len, skip=()):
    """Recognise structures inside one span, recursing into what is left over."""
    if start >= end:
        return []

    if "name_table" not in skip:
        nt = parse_name_table(blob, start, end)
        if nt:
            return ([_annotate(nt, parent)]
                    + _refine_span(blob, parent, start + nt["length"], end,
                                   blob_len))

    if "pointer_table" not in skip:
        pt = parse_pointer_table(blob, start, end, blob_len)
        if pt:
            return ([_annotate(pt, parent)]
                    + _refine_span(blob, parent, start + pt["length"], end,
                                   blob_len, skip=("pointer_table",)))

    if "key_table" not in skip:
        found = find_key_tables(blob, start, end)
        if found:
            out, cursor = [], start
            for off, count in found:
                if off > cursor:
                    out += _refine_span(blob, parent, cursor, off, blob_len,
                                        skip=RECOGNISERS)
                out.append(_key_table_region(blob, off, count, parent))
                cursor = off + count * 4
            out += _refine_span(blob, parent, cursor, end, blob_len,
                                skip=RECOGNISERS)
            return out

    return [_slice(parent, blob, start, end)]


def _annotate(region, parent):
    if "section" in parent:
        region["section"] = parent["section"]
    return region


def _refine(blob: bytes, regions):
    """Split opaque and section regions wherever a recogniser identifies something."""
    out = []
    for r in regions:
        if r["kind"] not in ("opaque", "section"):
            out.append(r)
            continue
        out += _refine_span(blob, r, r["offset"], r["offset"] + r["length"],
                            len(blob))
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


# --------------------------------------------------------------------------
# decompile

def decompile(raw: bytes, filename=None) -> dict:
    xml, sep, blob = split_container(raw)

    if blob[:4] != MAGIC:
        raise ConfigError(
            f"expected {MAGIC.decode()} magic, found {blob[:4]!r}. "
            "Only arch 9 is modelled so far; arch 8 blobs start with TPTP.")
    if blob[-4:] != END_MAGIC:
        raise ConfigError(f"expected {END_MAGIC.decode()} at the end, "
                          f"found {blob[-4:]!r}")

    marker = blob.find(HEADER_MARKER)
    if marker == -1:
        raise ConfigError("no CMAH marker in the header")
    header_len = marker + len(HEADER_MARKER)

    end_address = _u32(blob, 4)
    unknown_08 = _u32(blob, 8)

    # section pointers run until the first zero
    ptrs, off = [], PTR_TABLE_OFF
    while off + 4 <= marker:
        v = _u32(blob, off)
        if v == 0:
            break
        ptrs.append(v)
        off += 4
    padding = blob[off:marker]

    # the addresses tile the region from the first pointer to the end marker
    end_off = len(blob) - 4
    bounds = [p - CONFIG_BASE for p in ptrs] + [end_off]
    for i, o in enumerate(bounds[:-1]):
        if not (0 <= o < len(blob)):
            raise ConfigError(f"section {i} address 0x{ptrs[i]:X} is out of range")
        if bounds[i + 1] < o:
            raise ConfigError(f"section {i} is not followed by a later address")

    regions = [{
        "kind": "blob_header",
        "offset": 0,
        "length": header_len,
        "magic": MAGIC.decode(),
        "end_address": f"0x{end_address:06X}",
        "unknown_0x08": f"0x{unknown_08:08X}",
        "section_count": len(ptrs),
        "padding": padding.hex(),
        "marker": HEADER_MARKER.decode(),
    }]

    first_section = bounds[0]
    if first_section > header_len:
        regions.append({
            "kind": "opaque",
            "note": "not covered by the section table; holds the 114-record "
                    "array indexed by section 6",
            "offset": header_len,
            "length": first_section - header_len,
            "data": _hex_lines(blob[header_len:first_section]),
        })

    for i in range(len(ptrs)):
        a, b = bounds[i], bounds[i + 1]
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
        "magic": END_MAGIC.decode(),
    })

    regions = _refine(blob, regions)

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

def emit_region(r: dict) -> bytes:
    """Turn one region back into bytes."""
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

    if kind == "pointer_table":
        targets = r["targets"]
        out = bytearray()
        out += len(targets).to_bytes(r["count_width"], "little")
        out += bytes.fromhex(r["padding"])
        for o in targets:
            out += (o + CONFIG_BASE).to_bytes(3, "little")
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

    # lay the body out first, so section addresses can be derived from it
    body, addresses, cursor = [], {}, header["length"]
    for r in regions:
        kind = r["kind"]
        if kind in ("blob_header", "blob_footer"):
            continue

        # a section may have been split into several regions; its address is
        # where the first of them lands
        if "section" in r and r["section"] not in addresses:
            addresses[r["section"]] = cursor + CONFIG_BASE

        data = emit_region(r)
        body.append(data)
        cursor += len(data)

    if sorted(addresses) != list(range(len(addresses))):
        raise ConfigError("section indices must be contiguous from 0")

    end_address = cursor + CONFIG_BASE
    padding = bytes.fromhex(header["padding"])
    declared = int(header["unknown_0x08"], 16)

    out = bytearray()
    out += MAGIC
    out += _p32(end_address)
    out += _p32(declared)
    for i in range(len(addresses)):
        out += _p32(addresses[i])
    out += padding
    out += HEADER_MARKER

    if len(out) != header["length"]:
        raise ConfigError(
            f"rebuilt header is {len(out)} B, the source says "
            f"{header['length']} B - the pointer table or padding changed size")

    for chunk in body:
        out += chunk
    out += END_MAGIC
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
