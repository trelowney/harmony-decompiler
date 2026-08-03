"""Poll state variables and report anything that changes.

Addresses 0-6 are the clock and change on their own, so they are skipped.
Read only - same whitelist as hid_query.py.

Usage:
    python poll_state.py [seconds]
"""
import sys
import time

from hid_query import MISC_STATE, Remote

LOW, HIGH = 7, 48          # 0-6 are the clock
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def snapshot(rm):
    s = {}
    for a in range(LOW, HIGH):
        v = rm.read_misc_word(a, MISC_STATE)
        if v is None:
            break
        s[a] = v
    return s


def main():
    rm = Remote()
    try:
        base = snapshot(rm)
        if not base:
            print("No response from the remote.")
            return 1
        print(f"=== BASELINE (addresses {LOW}-{max(base)}) ===")
        print("  non-zero: " + (", ".join(f"[{a}]={v}" for a, v in base.items() if v)
                                or "none"))
        print(f"\n=== WATCHING {DURATION} s - PRESS BUTTONS ===\n")

        prev = dict(base)
        t_end = time.time() + DURATION
        rounds, changes = 0, 0
        while time.time() < t_end:
            cur = snapshot(rm)
            rounds += 1
            for a in sorted(cur):
                if a in prev and cur[a] != prev[a]:
                    changes += 1
                    print(f"  {time.strftime('%H:%M:%S')}  CHANGE address {a}: "
                          f"{prev[a]} -> {cur[a]}")
            prev = cur
            time.sleep(0.15)

        print(f"\n=== DONE ===")
        print(f"  rounds: {rounds}, changes: {changes}")
        diff = {a: (base[a], prev[a]) for a in base if base.get(a) != prev.get(a)}
        print(f"  net difference from the start: {diff or 'none'}")
    finally:
        rm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
