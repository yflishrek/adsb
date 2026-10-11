"""Regression tests for the collector's *silent* failure modes.

Both of these were invisible in production on 2026-10-11 and cost real campaign
data, so they are worth pinning down:

1. adsb.lol answers HTTP 200 with ``"ac": []`` when its feeders have no coverage
   for the query point.  The old code accepted that as "no traffic", wrote a
   219-byte header-only CSV and moved on.  18 of those in a row (08:37-11:34
   Beijing) left a 3-hour hole that looked like data in the repository.  The
   fix retries inside the cycle, counts the window, and writes no file.

2. Because OpenSky keeps working while adsb.lol is empty, a test on *total*
   rows never notices.  collect_longrun therefore tracks a source-specific
   consecutive-empty streak and warns.

No network access is required for 2 and 3; part 1 makes one live request and
skips itself if the API is unreachable.

Usage:
    python scripts/test_cloud_collect.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloud_collect as C

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- 1. live call
print("\n1. live adsb.lol request")
try:
    ac, err = C._adsblol_once()
    check("live request succeeds", err is None and ac is not None,
          f"aircraft={len(ac) if ac else 0} err={err}")
except Exception as e:                                   # pragma: no cover
    print(f"  [SKIP] {type(e).__name__}: {e}")

# ------------------------------------------- 2. valid-but-empty is retried
print("\n2. valid-but-empty response (the outage shape)")
tmp = tempfile.mkdtemp(prefix="adsbtest_")
C.DATA_DIR = tmp                      # keep the test out of data/flights/
C.ADSBLOL_RETRY_SLEEP = 0.0           # do not actually wait
calls = {"n": 0}


def empty_once(timeout=30):
    calls["n"] += 1
    return [], None


real_once = C._adsblol_once
C._adsblol_once = empty_once
C.STATS["adsblol_empty"] = 0
rows = C.collect_adsblol()
check("empty response yields 0 rows", rows == 0, f"rows={rows}")
check("empty response is retried the full count",
      calls["n"] == C.ADSBLOL_EMPTY_RETRIES,
      f"attempts={calls['n']}/{C.ADSBLOL_EMPTY_RETRIES}")
check("empty window is counted", C.STATS["adsblol_empty"] == 1,
      f"STATS.adsblol_empty={C.STATS['adsblol_empty']}")
check("no CSV is written for an empty window", not os.listdir(tmp),
      f"written={os.listdir(tmp) or '(none)'}")

# ------------------------------------------------- 3. transport failure counted
print("\n3. transport failure")
C.STATS["adsblol_fail"] = 0


def fail_once(timeout=30):
    return None, "ConnectTimeout: read timed out"


C._adsblol_once = fail_once
rows = C.collect_adsblol()
check("transport failure yields 0 rows and is counted",
      rows == 0 and C.STATS["adsblol_fail"] == 1,
      f"rows={rows} STATS.adsblol_fail={C.STATS['adsblol_fail']}")
check("no CSV is written for a failed window", not os.listdir(tmp))
C._adsblol_once = real_once

# ------------------------------- 4. the driver notices a source-specific outage
print("\n4. collect_longrun notices adsb.lol-only outage")
import collect_longrun as CL

calls2 = {"a": 0, "b": 0, "commits": 0}
CL.collect_adsblol = lambda: (calls2.__setitem__("a", calls2["a"] + 1), 0)[1]
CL.collect_opensky = lambda: (calls2.__setitem__("b", calls2["b"] + 1), 40)[1]
CL.commit_and_push = lambda tag: calls2.__setitem__("commits", calls2["commits"] + 1)
sys.argv = ["collect_longrun.py", "--interval", "1", "--max-minutes", "0.1",
            "--commit-every", "999"]
os.environ["DATA_BATCH"] = "TESTBATCH"
rc = CL.main()
check("driver exits 0 during a source outage", rc == 0, f"rc={rc}")
check("driver kept polling", calls2["a"] >= 5, f"cycles={calls2['a']}")
check("only the final flush committed", calls2["commits"] == 1,
      f"commits={calls2['commits']}")

print("\n" + ("ALL TESTS PASSED" if not failures
              else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)
