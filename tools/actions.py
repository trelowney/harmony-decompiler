"""What can be said about the action list instruction set, from the data alone.

    python tools/actions.py samples/harmony525/config.bin
    python tools/actions.py --all

Section 10 indexes an array of action lists, each `<u8 count> <u16 operand>
<u8 opcode>[count]`, and a key table's target selects one of them - see
docs/FORMAT.md section 4i. What the opcodes do is not known. This prints the
evidence that is available without a remote, a disassembler or a guess:

  - how often each opcode occurs, and where in a list it sits
  - whether its operand stays inside a range that means something, such as the
    number of action lists or the number of records
  - whether it is a negative number when read as signed

None of that names an opcode on its own. Taken together it says a lot about
which are references, which are parameters and which are structure.
"""
import collections
import sys
from pathlib import Path

import _paths  # noqa: F401
import hconfig

SIGNED = 0x8000


def profile(path: Path):
    doc = hconfig.decompile(path.read_bytes(), path.name)
    regions = doc["blob"]["regions"]
    lists = [r for r in regions if r["kind"] == "action_list"]
    if not lists:
        print(f"{path.name}: no action lists found")
        return

    n_lists = len(lists)
    n_records = len([r for r in regions if r["kind"] == "record_header"])
    key_targets = {e["target"] for r in regions if r["kind"] == "key_table"
                   for e in r["entries"]}

    ops = collections.defaultdict(list)
    where = collections.defaultdict(collections.Counter)
    for r in lists:
        last = len(r["instructions"]) - 1
        for k, ins in enumerate(r["instructions"]):
            ops[ins["opcode"]].append(ins["operand"])
            where[ins["opcode"]]["first" if k == 0 else
                                 "last" if k == last else "middle"] += 1

    total = sum(len(v) for v in ops.values())
    print(f"\n=== {path.name} ===")
    print(f"{n_lists} action lists, {total} instructions, "
          f"{n_records} records, {len(key_targets)} lists bound to a key")
    print()
    print(f"{'op':<6}{'count':>6}{'uniq':>6}{'min':>8}{'max':>8}"
          f"{'<lists':>8}{'<recs':>7}{'signed<0':>10}  position")
    for op in sorted(ops, key=lambda o: -len(ops[o])):
        v = ops[op]
        pct = lambda f: 100 * sum(1 for x in v if f(x)) / len(v)
        pos = " ".join(f"{k}={n}" for k, n in where[op].most_common())
        print(f"{op:<6}{len(v):>6}{len(set(v)):>6}{min(v):>8}{max(v):>8}"
              f"{pct(lambda x: x < n_lists):>7.0f}%"
              f"{pct(lambda x: x < n_records):>6.0f}%"
              f"{pct(lambda x: x >= SIGNED):>9.0f}%  {pos}")

    # The two things this turns up that are worth stating on their own.
    call = ops.get("0x7F", [])
    if call:
        distinct = set(call)
        print(f"\n0x7F: {len(call)} instructions, every operand a valid list "
              f"index ({all(x < n_lists for x in call)}).")
        print(f"      {len(distinct)} distinct lists called this way; "
              f"{len(distinct & key_targets)} of them are also bound to a key.")
        print("      So the array holds two nearly disjoint populations: lists a "
              "key binding\n      enters, and lists only ever reached from "
              "another list.")

    negative = [op for op, v in ops.items()
                if len(v) >= 5 and all(x >= SIGNED for x in v)]
    if negative:
        print(f"\nalways negative when read as signed: {', '.join(sorted(negative))}")
        for op in sorted(negative):
            v = sorted({x - 0x10000 for x in ops[op]})
            print(f"      {op}  {len(v)} distinct, {v[0]} to {v[-1]}")


def main(argv):
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args == ["--all"]:
        root = Path(__file__).resolve().parent.parent / "samples"
        files = sorted(p for p in root.rglob("*")
                       if p.suffix.lower() in (".ezhex", ".bin"))
    else:
        files = [Path(a) for a in args]
    for p in files:
        profile(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
