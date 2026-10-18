"""Write web/irdb-index.json, the list of Flipper-IRDB files the page searches.

    python build_flipper_index.py <Flipper-IRDB checkout>

The page fetches a chosen file from raw.githubusercontent.com at the commit
named here, so the index and the files always agree. Flipper-IRDB is CC0.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    db = pathlib.Path(sys.argv[1])
    commit = subprocess.run(["git", "-C", str(db), "rev-parse", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    files = sorted(p.relative_to(db).as_posix() for p in db.rglob("*.ir"))
    out = {"repo": "Lucaslhm/Flipper-IRDB", "commit": commit, "files": files}
    (ROOT / "web/irdb-index.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(files)} files at {commit[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
