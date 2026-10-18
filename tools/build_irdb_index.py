"""Write web/irdb-probonopd-index.json, the list of irdb files the page searches.

    python build_irdb_index.py <irdb checkout>

The page fetches a chosen file from raw.githubusercontent.com at the commit
named here. Contains/accesses irdb by Simon Peter and contributors, used under
permission; see https://github.com/probonopd/irdb for its license.
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
    files = sorted(p.relative_to(db).as_posix() for p in (db / "codes").rglob("*.csv"))
    out = {"repo": "probonopd/irdb", "commit": commit, "files": files}
    (ROOT / "web/irdb-probonopd-index.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(files)} files at {commit[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
