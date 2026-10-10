"""BTH ADS-B daily health check.

Reports sampling completeness, gaps, hourly continuity and aircraft-type
coverage for one day (or all days) of collected data.

Usage:
    python scripts/daily_check.py --date 20261010
    python scripts/daily_check.py --all
    python scripts/daily_check.py --date 20261010 --remote   # fetch from GitHub if local missing
    python scripts/daily_check.py --all --csv                # also write outputs/daily_check.csv

Expected sampling: one snapshot every --interval seconds (default 30), which
means 2880 cycles/day. This script compares nominal vs actual.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ADSB_DIR = os.path.join(ROOT, "data", "flights", "adsblol")
OSKY_DIR = os.path.join(ROOT, "data", "flights", "opensky")
MERGED_DIR = os.path.join(ROOT, "data", "flights", "merged")
OUT_DIR = os.path.join(ROOT, "outputs")

RAW_BASE = "https://raw.githubusercontent.com/yflishrek/adsb/master/"
API_BASE = "https://api.github.com/repos/yflishrek/adsb/contents/"


def download_remote(rel_path, local_path, tries=3):
    """Fetch a file from GitHub. Tries raw first, then the API (Contents).
    The local proxy fails intermittently, so retrying is essential."""
    import base64

    import requests
    for attempt in range(1, tries + 1):
        for kind, url in (("api", API_BASE + rel_path + "?ref=master"),
                          ("raw", RAW_BASE + rel_path)):
            try:
                r = requests.get(url, timeout=45)
                if kind == "api" and r.status_code == 200:
                    content = base64.b64decode(r.json()["content"])
                elif kind == "raw" and r.status_code == 200:
                    content = r.content
                else:
                    continue
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(content)
                print(f"  downloaded via {kind} -> {local_path} "
                      f"({len(content)/1024:.0f} KB)")
                return True
            except Exception as e:
                print(f"  attempt {attempt}/{kind}: {type(e).__name__} "
                      f"{str(e)[:60]}")
    print(f"  !! 无法下载 {rel_path}（代理不稳定，建议重试或用 git pull）")
    return False


def load(kind, date, remote):
    """Load one day of data for a source. Returns DataFrame or None."""
    d = {"adsblol": ADSB_DIR, "opensky": OSKY_DIR}[kind]
    path = os.path.join(d, f"{kind}_{date}.csv")
    if not os.path.exists(path) and remote:
        download_remote(f"data/flights/{kind}/{kind}_{date}.csv", path)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, low_memory=False)
    df["ts"] = pd.to_datetime(df["fetch_ts"], errors="coerce", utc=True)
    return df.dropna(subset=["ts"])


def cycle_stats(df, interval):
    """Cycles, completeness vs nominal, and the longest gaps."""
    cycles = df.groupby("fetch_ts").size()
    start, end = cycles.index.min(), cycles.index.max()
    t0 = pd.to_datetime(start, utc=True)
    t1 = pd.to_datetime(end, utc=True)
    elapsed = (t1 - t0).total_seconds()
    expected = int(elapsed // interval) + 1
    actual = len(cycles)

    # per-cycle timestamps sorted, find gaps > 3x interval
    stamps = sorted(pd.to_datetime(cycles.index, utc=True))
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        gap = (b - a).total_seconds()
        if gap > interval * 3:
            gaps.append((gap, a, b))
    gaps.sort(reverse=True)
    return {
        "start": t0, "end": t1, "expected": expected, "actual": actual,
        "completeness": actual / expected if expected else 0,
        "gaps": gaps[:5],
        "avg_rows": len(df) / actual if actual else 0,
    }


def hourly_cycles(df):
    """Distinct cycles per hour-of-day (UTC)."""
    s = df.groupby("fetch_ts").size().reset_index()
    s["ts"] = pd.to_datetime(s["fetch_ts"], utc=True)
    return s.groupby(s["ts"].dt.hour).size()


def type_coverage(date, osky, adsb):
    """Two numbers: (a) against the cumulative registry, (b) same-day overlap."""
    same_day = "n/a"
    if adsb is not None:
        adsb_hex = set(adsb["hex"].dropna().astype(str).str.strip().str.lower())
        icao = set(osky["icao24"].dropna().astype(str).str.strip().str.lower())
        if icao:
            same_day = (f"{(len(icao & adsb_hex)/len(icao))*100:.1f}% "
                        f"({len(icao & adsb_hex)}/{len(icao)})")

    mp = os.path.join(MERGED_DIR, f"merged_{date}.csv")
    if os.path.exists(mp):
        m = pd.read_csv(mp, low_memory=False)
        if "aircraft_type" in m.columns:
            hit = m["aircraft_type"].notna().mean()
            return (f"{hit*100:.1f}% (合并结果, {m['aircraft_type'].notna().sum()}"
                    f"/{len(m)})", same_day)

    registry = os.path.join(MERGED_DIR, "aircraft_types.csv")
    n_reg = 0
    if os.path.exists(registry):
        reg = pd.read_csv(registry, low_memory=False)
        n_reg = len(reg)
        reg_icao = set(reg.iloc[:, 0].dropna().astype(str).str.strip().str.lower())
        icao = set(osky["icao24"].dropna().astype(str).str.strip().str.lower())
        hit = len(icao & reg_icao) / len(icao) if icao else 0
        return (f"{hit*100:.1f}% (累积字典 {n_reg} 架, "
                f"命中 {len(icao & reg_icao)}/{len(icao)})", same_day)
    return ("字典不存在，先跑 python scripts/merge_sources.py", same_day)


def verdict(completeness, cov_str):
    """Plain-language health judgement."""
    v = []
    v.append("采样 OK" if completeness >= 0.95 else
             "采样偏低" if completeness >= 0.80 else "采样严重缺失")
    try:
        cov = float(cov_str.split("%")[0])
    except Exception:
        cov = None
    if cov is not None:
        v.append("机型覆盖充足" if cov >= 70 else
                 "机型覆盖可接受" if cov >= 50 else "机型覆盖不足")
    return " / ".join(v)


def check_one(date, args, sink):
    print("=" * 64)
    print(f"体检日期 {date}   (时间戳为 UTC；北京时间 = UTC+8)")
    print("=" * 64)

    adsb = load("adsblol", date, args.remote)
    osky = load("opensky", date, args.remote)
    if adsb is None and osky is None:
        print("  无数据文件。若是当天数据未在云端生成，请确认 Actions 是否运行。")
        print(f"  本地拉取命令：cd {ROOT} && git pull")
        return

    for name, df in [("adsb.lol", adsb), ("OpenSky", osky)]:
        if df is None:
            print(f"\n[{name}] 无数据文件")
            continue
        st = cycle_stats(df, args.interval)
        icao_col = "hex" if name == "adsb.lol" else "icao24"
        print(f"\n[{name}]")
        print(f"  记录行数        : {len(df):,}")
        print(f"  快照轮次        : {st['actual']:,} (应有 {st['expected']:,}，"
              f"完整率 {st['completeness']*100:.1f}%)")
        print(f"  每轮平均飞机数  : {st['avg_rows']:.1f}")
        print(f"  唯一航空器      : {df[icao_col].nunique():,}")
        print(f"  覆盖时段        : {st['start']:%H:%M:%S} → {st['end']:%H:%M:%S} UTC "
              f"({st['end']-st['start']})")
        if st["gaps"]:
            print("  最长数据缺口:")
            for g, a, b in st["gaps"]:
                print(f"    {g/60:6.1f} 分钟  {a:%H:%M:%S} → {b:%H:%M:%S} UTC")
        else:
            print("  最长数据缺口    : 无异常间隙")

        sink.append({"date": date, "source": name, "rows": len(df),
                     "cycles": st["actual"], "expected": st["expected"],
                     "completeness": round(st["completeness"] * 100, 1),
                     "aircraft": df[icao_col].nunique()})

    if osky is not None:
        cov, same_day = type_coverage(date, osky, adsb)
        comp = min(cycle_stats(df, args.interval)["completeness"]
                   for df in [adsb, osky] if df is not None)
        print("\n[综合]")
        print(f"  机型覆盖(累积)  : {cov}")
        print(f"  机型覆盖(当日)  : {same_day}")
        print(f"  判定            : {verdict(comp, cov)}")
        try:
            if float(cov.split("%")[0]) < 50:
                print("  → 累积字典仍偏旧，建议刷新：python scripts/merge_sources.py")
        except Exception:
            pass

    print(f"\n提示：完整率是相对首末采样时间计算的，若 Actions 当天尚未跑满，"
          f"该值会偏低，属正常。")


def main():
    ap = argparse.ArgumentParser(description="BTH ADS-B daily health check")
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--all", action="store_true", help="check every day present")
    ap.add_argument("--remote", action="store_true",
                    help="download from GitHub when local file is missing")
    ap.add_argument("--interval", type=int, default=30,
                    help="nominal polling interval in seconds (default 30)")
    ap.add_argument("--csv", action="store_true", help="write summary to outputs/")
    args = ap.parse_args()

    if args.all:
        dates = sorted({f.split("_")[1][:8] for f in
                        os.listdir(ADSB_DIR) + os.listdir(OSKY_DIR)
                        if f.endswith(".csv")})
        if not dates:
            print("没有找到任何数据文件。")
            sys.exit(1)
    elif args.date:
        dates = [args.date]
    else:
        today = datetime.utcnow().strftime("%Y%m%d")
        dates = [today]

    sink = []
    for d in dates:
        check_one(d, args, sink)

    if args.csv and sink:
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, "daily_check.csv")
        pd.DataFrame(sink).to_csv(out, index=False)
        print(f"\n汇总已写入: {out}")


if __name__ == "__main__":
    main()
