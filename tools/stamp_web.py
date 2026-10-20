"""Stamp the page's scripts with their content hashes, so a browser never runs
yesterday's module from its cache after a change (GitHub Pages lets it keep a
file for ten minutes). Run before committing a change to web/.

    python stamp_web.py
"""
import hashlib
import pathlib
import re

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"


def digest(name: str) -> str:
    return hashlib.sha256((WEB / name).read_bytes()).hexdigest()[:10]


def stamp(holder: str, name: str) -> None:
    path = WEB / holder
    text = path.read_text(encoding="utf-8")
    new = re.sub(rf'(["\']\.?/?{re.escape(name)})(\?v=[0-9a-f]+)?(["\'])',
                 rf"\g<1>?v={digest(name)}\g<3>", text)
    path.write_text(new, encoding="utf-8", newline="\n")


def main() -> int:
    stamp("app.js", "hid525.js")
    stamp("app.js", "remote.js")
    stamp("app.js", "pyworker.js")
    stamp("index.html", "app.js")        # after app.js has its own stamps
    print("stamped:", digest("app.js"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
