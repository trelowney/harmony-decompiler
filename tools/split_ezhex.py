"""Split an .EZHex into its XML header and binary blob, and verify integrity.

Container format per libconcord/operationfile.cpp:find_config_binary():
  - the blob starts two bytes after the closing </INFORMATION> tag
  - <BINARYDATASIZE> must equal the real blob length
  - <CHECKSUM> is an XOR of every blob byte, seeded 0x69

Usage:
    python split_ezhex.py [file.EZHex] [output-dir]

Exits non-zero if either check fails.
"""
import sys
import xml.dom.minidom
from pathlib import Path

from _paths import END_TAG, SAMPLE_EZHEX


def split(path: Path):
    raw = path.read_bytes()
    idx = raw.find(END_TAG)
    if idx == -1:
        raise SystemExit(f"{path.name}: no </INFORMATION> tag found")

    # libconcord: binary_ptr = end of tag + 2
    blob_start = idx + len(END_TAG) + 2
    xml_bytes = raw[:blob_start]
    blob = raw[blob_start:]
    return raw, xml_bytes, blob, raw[idx + len(END_TAG):blob_start]


def get_tag(xml_bytes: bytes, tag: str):
    open_t, close_t = f"<{tag}>".encode(), f"</{tag}>".encode()
    a = xml_bytes.find(open_t)
    if a == -1:
        return None
    b = xml_bytes.find(close_t, a)
    return xml_bytes[a + len(open_t):b].decode("ascii", "replace").strip()


def main(path: Path, outdir: Path):
    raw, xml_bytes, blob, sep = split(path)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"file            : {path.name} ({len(raw)} B)")
    print(f"XML part        : {len(xml_bytes)} B")
    print(f"separator       : {sep!r}")
    print(f"binary blob     : {len(blob)} B")
    print()

    # --- size check ---
    declared = get_tag(xml_bytes, "BINARYDATASIZE")
    print(f"BINARYDATASIZE  : {declared}")
    size_ok = declared is not None and int(declared) == len(blob)
    print(f"  -> actual     : {len(blob)}  [{'OK' if size_ok else 'MISMATCH'}]")

    # --- checksum check ---
    declared_ck = get_tag(xml_bytes, "CHECKSUM")
    calc = 0x69
    for byte in blob:
        calc ^= byte
    print(f"CHECKSUM        : {declared_ck}")
    ck_ok = declared_ck is not None and int(declared_ck) == calc
    print(f"  -> computed   : {calc} (0x{calc:02X})  [{'OK' if ck_ok else 'MISMATCH'}]")
    print()

    (outdir / "config_header.xml").write_bytes(xml_bytes)
    (outdir / "config_blob.bin").write_bytes(blob)

    try:
        pretty = xml.dom.minidom.parseString(xml_bytes).toprettyxml(indent="  ")
        pretty = "\n".join(l for l in pretty.splitlines() if l.strip())
        (outdir / "config_header_pretty.xml").write_text(pretty, encoding="utf-8")
        print(f"XML is valid, {len(pretty.splitlines())} lines after formatting")
    except Exception as e:
        print(f"XML failed to parse: {e}")

    print(f"written to {outdir}")
    return 0 if (size_ok and ck_ok) else 1


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE_EZHEX
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out")
    sys.exit(main(src, dst))
