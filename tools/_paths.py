"""Shared path resolution, so no script has to hardcode a location.

Every analysis script takes an optional path argument and falls back to the
sample config shipped in this repository, which is what all offsets quoted in
docs/FORMAT.md refer to.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SAMPLE_DIR = REPO / "samples" / "harmony525"
SAMPLE_BLOB = SAMPLE_DIR / "config.bin"
SAMPLE_EZHEX = SAMPLE_DIR / "config.EZHex"

# Arch 8 samples (720/785/88x) are not redistributed here. Download
# EZHex.Samples.zip from the concordance thread and unpack it into this
# directory if you want the cross-architecture comparisons to run:
#   https://github.com/user-attachments/files/22412763/EZHex.Samples.zip
ARCH8_DIR = REPO / "samples" / "arch8"

END_TAG = b"</INFORMATION>"
CONFIG_BASE = 0x20000


def get_blob(path=None) -> bytes:
    """Return the binary blob, given either an .EZHex or an already-split .bin.

    The container is XML followed by the blob, starting two bytes after
    </INFORMATION> - per libconcord/operationfile.cpp:find_config_binary.
    """
    p = Path(path) if path else SAMPLE_BLOB
    if not p.exists():
        raise SystemExit(f"not found: {p}")
    raw = p.read_bytes()
    i = raw.find(END_TAG)
    return raw[i + len(END_TAG) + 2:] if i >= 0 else raw


def arch8_samples():
    """Arch 8 sample files, or an empty list with a hint printed."""
    if not ARCH8_DIR.exists():
        print(f"note: {ARCH8_DIR} does not exist - arch 8 comparisons skipped.")
        print("      See the comment in tools/_paths.py for where to get them.")
        return []
    return sorted(ARCH8_DIR.glob("*.EZHex"))


def hexdump(data, off, length, label=None):
    """Print a classic 16-byte-per-line hexdump of data[off:off+length]."""
    if label:
        print(f"\n=== {label}  (0x{off:06X}, {length} B) ===")
    for base in range(off, min(off + length, len(data)), 16):
        chunk = data[base:base + 16]
        hexs = " ".join(f"{b:02X}" for b in chunk).ljust(47)
        txt = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{base:06X}  {hexs}  |{txt}|")
