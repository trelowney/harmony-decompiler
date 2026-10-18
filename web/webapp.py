"""The Python half of the web page, run in the browser by Pyodide.

It reads a configuration with the same modules the command line uses
(tools/), so the page and the tests agree by construction.
"""
import hashlib

import compose525
import hconfig
import render_525_screens as renderer
import verify_525_semantics as sem
from clone_525_device import mode_fields

_TEMPLATE = None


def set_template(container: bytes) -> None:
    """The public sample's .EZHex, whose XML header a dumped blob is wrapped in."""
    global _TEMPLATE
    _TEMPLATE = hconfig.split_container(bytes(container))


def ezhex_from_blob(blob: bytes) -> bytes:
    """Wrap a blob read off a remote the way concordance --dump-config does: the
    header with BINARYDATASIZE and CHECKSUM recomputed."""
    xml, sep, _ = _TEMPLATE
    blob = bytes(blob)
    xml = hconfig._set_tag(xml, "BINARYDATASIZE", len(blob))
    xml = hconfig._set_tag(xml, "CHECKSUM", hconfig.blob_checksum(blob))
    return xml + sep + blob


def summary(container: bytes) -> dict:
    raw = bytes(container)
    blob = hconfig.split_container(raw)[2]
    trailer_ok = int.from_bytes(blob[-6:-4], "little") == hconfig.trailer_checksum(blob)
    roundtrip_ok = hconfig.compile_config(hconfig.decompile(raw, "page")) == raw
    devices = [d.__dict__ for d in compose525.devices(raw)]
    try:
        activities = [a.__dict__ for a in compose525.activities(raw)]
    except compose525.ComposeError:
        activities = []
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blob_bytes": len(blob),
        "trailer_ok": trailer_ok,
        "roundtrip_ok": roundtrip_ok,
        "devices": devices,
        "activities": activities,
    }


def screens(container: bytes, modes) -> list:
    """Rendered pages of the given modes: (mode, page, 96 x 64 bytes, 1 = ink)."""
    blob = hconfig.split_container(bytes(container))[2]
    S = sem.section_offsets(blob)
    fonts = renderer.fonts(blob, S)
    out = []
    for mode in modes:
        for k, page in enumerate(mode_fields(blob, S, mode)["pages"]):
            canvas, _ = renderer.render_page(blob, fonts, page["program"])
            pixels = bytes(1 if v == renderer.INK else 0 for row in canvas for v in row)
            out.append((mode, k, pixels))
    return out


def screen_modes(container: bytes) -> list:
    """The modes worth showing: the Devices screen, each device, the activity menu."""
    raw = bytes(container)
    d = compose525._Doc(raw)
    modes = [d.devices_modes()[0]]
    modes += sorted({x.mode for x in compose525.devices(raw)})
    try:
        modes.append(compose525._menu_mode(d))
    except compose525.ComposeError:
        pass
    return modes
