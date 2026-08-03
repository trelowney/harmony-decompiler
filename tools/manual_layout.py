"""Extract labels from the remote diagram in a user manual, WITH COORDINATES.

The diagram on page 5 of the 525 manual is vector art, so the button labels are
real text with a position. That makes it possible to reconstruct the physical
layout of the remote - which is one half of what is needed to solve the matrix
mapping. (The other half, which cell each switch is wired to, is still open; see
docs/OPEN-QUESTIONS.md.)

The manual is not redistributed here. Logitech's documentation server is still
alive at the time of writing:

  https://images.harmonyremote.com/EasyZapper/Downloads/UserManual/525/enu/525_UserManual.pdf

Requires pypdf. Note that extracting embedded *images* from that page returns
nothing - it is vector, not a bitmap - so this coordinate approach is the one
that works.

Usage:
    python manual_layout.py path/to/manual.pdf [page]
"""
import sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    raise SystemExit("needs pypdf:  pip install pypdf")

if len(sys.argv) < 2:
    raise SystemExit(__doc__.strip().splitlines()[-1])

PDF = Path(sys.argv[1])
PAGE = int(sys.argv[2]) if len(sys.argv) > 2 else 5

reader = PdfReader(PDF)
page = reader.pages[PAGE - 1]
print(f"page {PAGE}, size {page.mediabox.width:.0f} x {page.mediabox.height:.0f}\n")

items = []


def visitor(text, cm, tm, font_dict, font_size):
    t = text.strip()
    if t:
        items.append((tm[4], tm[5], font_size, t))


page.extract_text(visitor_text=visitor)

# --- prose (long sentences) versus diagram callouts (short labels) ---
labels = [it for it in items if len(it[3]) <= 12]
print(f"total fragments: {len(items)},  short labels: {len(labels)}\n")

print("=== SHORT LABELS BY POSITION (top to bottom) ===")
print(f"{'x':>7} {'y':>7} {'size':>5}  text")
for x, y, fs, t in sorted(labels, key=lambda i: (-i[1], i[0])):
    print(f"{x:7.1f} {y:7.1f} {fs:5.1f}  {t!r}")
