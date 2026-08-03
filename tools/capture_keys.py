"""Capture remote state during key presses, grouped by pauses between them.

The known result for the 525 is negative and is recorded in FORMAT.md §5d: in USB
mode the remote ignores most keys entirely, and the keys it does react to only
light the display. Two *different* keys produce an identical trace - address 9
going 0 -> 1 and back after exactly 10 s, which is the backlight timeout. The
remote never exposes which key was pressed.

Note on the design: an earlier version of this script polled without a pause and
captured nothing, which was indistinguishable from nobody pressing anything. This
version keeps the slower sweep that demonstrably works, counts rounds and failed
reads, and reports every change as it happens - so a blind run cannot be mistaken
for a negative result again. If the failure rate is high, it says so and tells you
not to trust the output.

Read only - same whitelist as hid_query.py.

Usage:
    python capture_keys.py [seconds]
"""
import sys
import time

from hid_query import MISC_STATE, Remote

LOW, HIGH = 7, 48
SLEEP = 0.15
GAP_S = 6.0
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 100


def sweep(rm):
    """Return (state, number of failed reads)."""
    s, fails = {}, 0
    for a in range(LOW, HIGH):
        v = rm.read_misc_word(a, MISC_STATE)
        if v is None:
            fails += 1
            continue
        s[a] = v
    return s, fails


def main():
    rm = Remote()
    try:
        base, fails = sweep(rm)
        print(f"=== BASELINE ({len(base)} addresses, {fails} failures) ===")
        print("  non-zero: " + (", ".join(f"[{a}]={v}" for a, v in base.items() if v)
                                or "none"))
        print(f"\n=== CAPTURING {DURATION} s - PRESS BUTTONS ===\n")
        sys.stdout.flush()

        events = []
        prev = dict(base)
        rounds = total_fails = 0
        t_end = time.time() + DURATION
        next_beat = time.time() + 15

        while time.time() < t_end:
            cur, f = sweep(rm)
            rounds += 1
            total_fails += f
            changed = {a: (prev.get(a), cur[a]) for a in cur
                       if a in prev and cur[a] != prev[a]}
            if changed:
                events.append({"t": time.time(),
                               "diff": {a: cur[a] for a in cur
                                        if base.get(a) != cur[a]},
                               "changed": changed})
                print(f"  {time.strftime('%H:%M:%S')}  CHANGE " +
                      ", ".join(f"[{a}] {o}->{n}" for a, (o, n) in changed.items()))
                sys.stdout.flush()
            prev = cur
            if time.time() >= next_beat:
                print(f"  ... {rounds} rounds, {total_fails} failed reads, "
                      f"{len(events)} events")
                sys.stdout.flush()
                next_beat += 15
            time.sleep(SLEEP)

        print(f"\n=== DIAGNOSTICS ===")
        print(f"  rounds: {rounds}   failed reads: {total_fails}   "
              f"events: {len(events)}")
        if rounds and total_fails / (rounds * (HIGH - LOW)) > 0.1:
            print("  !! high read failure rate - do not trust these results")

        if not events:
            print("\n  No changes. Either nothing was pressed, or the remote does"
                  " not reflect key presses into state variables in USB mode -"
                  " which is what it does on the 525.")
            return 0

        # --- split into groups separated by a pause ---
        groups, g = [], [events[0]]
        for e in events[1:]:
            if e["t"] - g[-1]["t"] >= GAP_S:
                groups.append(g)
                g = [e]
            else:
                g.append(e)
        groups.append(g)

        print(f"\n=== {len(groups)} GROUPS ===")
        for i, grp in enumerate(groups, 1):
            span = grp[-1]["t"] - grp[0]["t"]
            print(f"\n--- GROUP {i}: {len(grp)} events, {span:.1f} s ---")
            keys = sorted({a for e in grp for a in e["diff"]})
            if not keys:
                print("  no address differs from the baseline")
                continue
            for a in keys:
                vals = [e["diff"].get(a, base.get(a)) for e in grp]
                uniq = sorted(set(vals))
                print(f"  address {a:3d}: values {vals}   distinct {uniq}")
    finally:
        rm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
