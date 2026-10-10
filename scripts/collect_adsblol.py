#!/usr/bin/env python3
"""
adsb.lol - BTH 3-airport ADS-B real-time collector
====================================================
Collects aircraft data near PEK/PKX/TSN from adsb.lol API.
Data saved to D:/PARTD/data/flights/adsblol/, one CSV per day.

用法：
    python collect_adsblol.py                    # 默认启动采集
    python collect_adsblol.py --interval 60       # 自定义间隔（秒）
    python collect_adsblol.py --dry-run           # 试跑一次看看数据

依赖：
    pip install requests
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# BTH 中心点坐标（覆盖PEK/PKX/TSN三机场，半径80海里）
BTH_CENTER = {"lat": 39.6, "lon": 116.8}

# adsb.lol API 端点
ADSB_BASE = "https://api.adsb.lol"
NEARBY_URL = f"{ADSB_BASE}/v2/lat/{{lat}}/lon/{{lon}}/dist/{{dist}}"

DEFAULT_INTERVAL = 30      # 轮询间隔（秒）
SEARCH_RADIUS = 80          # 搜索半径（海里），覆盖三机场

# 输出目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data", "flights", "adsblol")

# CSV 字段 — 对应 adsb.lol API 实际返回的字段名
CSV_FIELDS = [
    "fetch_ts",        # 采集时间戳 (ISO 8601)
    "zone",            # 查询区域 (BTH)
    "hex",             # ICAO24 地址（原字段: hex）
    "flight",          # 航班号（原字段: flight）
    "r",               # 注册号（原字段: r）
    "t",               # 机型（原字段: t）
    "category",        # 飞机类别（原字段: category）
    "lat",             # 纬度
    "lon",             # 经度
    "alt_baro",        # 气压高度 (英尺)
    "alt_geom",        # 几何高度 (英尺)
    "gs",              # 地速 (节)
    "ias",             # 指示空速 (节)
    "tas",             # 真空速 (节)
    "mach",            # 马赫数
    "track",           # 航迹 (度)
    "mag_heading",     # 磁航向 (度)
    "true_heading",    # 真航向 (度)
    "baro_rate",       # 垂直速率 (英尺/分)
    "squawk",          # 应答机编码
    "emergency",       # 紧急状态
    "nav_qnh",         # 修正海压 (hPa)
    "nav_altitude_mcp",# MCP/FCU 设定高度 (英尺)
    "wind_dir",        # 风速来源方向
    "wind_speed",      # 风速 (节)
    "oat",             # 外界大气温度 (°C)
    "seen_pos",        # 上次位置更新距今 (秒)
    "messages",        # 消息数
    "rssi",            # 信号强度 (dB)
    "dst",             # 距查询点距离 (海里)
    "dir",             # 相对方位 (度)
]

# API 原始字段到 CSV 字段的映射（部分字段同名，不再逐项列出）
FIELD_MAP = {
    "wd": "wind_dir",
    "ws": "wind_speed",
}

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] adsblol: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("adsblol-collector")


def fetch_nearby(lat: float, lon: float, radius: int = SEARCH_RADIUS) -> List[Dict]:
    """查询指定坐标周边飞机"""
    url = NEARBY_URL.format(lat=lat, lon=lon, dist=radius)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # adsb.lol 返回格式: {"ac": [...], "total": N, ...}
        return data.get("ac", [])
    except requests.exceptions.RequestException as e:
        log.warning(f"API 请求失败 [{lat},{lon}]: {e}")
        return []
    except json.JSONDecodeError as e:
        log.warning(f"JSON 解析失败 [{lat},{lon}]: {e}")
        return []


def normalize_aircraft(ac: Dict) -> Dict:
    """将 API 返回的飞机字典标准化为 CSV 行"""
    row = {
        "fetch_ts": datetime.now(timezone.utc).isoformat(),
        "zone": "BTH",
    }
    # 将 adsb.lol 原始字段映射到 CSV 列
    for csv_col in CSV_FIELDS:
        if csv_col in row:
            continue  # 已填写的字段跳过
        # 尝试同名映射
        if csv_col in ac:
            val = ac[csv_col]
            if isinstance(val, str):
                val = val.strip()
            row[csv_col] = val
        # 旧名 → 新名映射
        elif csv_col in FIELD_MAP and FIELD_MAP[csv_col] in ac:
            val = ac[FIELD_MAP[csv_col]]
            if isinstance(val, str):
                val = val.strip()
            row[csv_col] = val
        else:
            row[csv_col] = None

    # 强制类型：hex 和 flight 大写去空格
    if "hex" in row and row["hex"]:
        row["hex"] = str(row["hex"]).strip().upper()
    if "flight" in row and row["flight"]:
        row["flight"] = str(row["flight"]).strip().upper()

    return row


def get_today_csv_path() -> str:
    """获取今天的 CSV 文件路径，按天分割"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return os.path.join(DATA_DIR, f"adsblol_{today}.csv")


def write_row(path: str, row: Dict):
    """追加一行到 CSV 文件（自动写表头）"""
    file_exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def run_collect_cycle():
    """
    执行一次采集周期：从BTH中心点查询周边飞机，写入 CSV
    返回本次采集到的飞机总数
    """
    seen_keys = set()   # 去重
    total_rows = 0
    csv_path = get_today_csv_path()

    aircraft = fetch_nearby(BTH_CENTER["lat"], BTH_CENTER["lon"], SEARCH_RADIUS)
    for ac in aircraft:
        hex_code = ac.get("hex", "").strip().upper()
        if not hex_code:
            continue
        if hex_code in seen_keys:
            continue
        seen_keys.add(hex_code)
        row = normalize_aircraft(ac)
        write_row(csv_path, row)
        total_rows += 1

    return total_rows


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="adsb.lol BTH 三机场 ADS-B 实时采集器"
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=DEFAULT_INTERVAL,
        help=f"轮询间隔（秒，默认 {DEFAULT_INTERVAL}）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="试跑一次，打印结果后退出"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="只采集一次，不进入循环"
    )
    args = parser.parse_args()

    log.info(f"{'='*60}")
    log.info(f"adsb.lol BTH 采集器启动")
    log.info(f"轮询间隔: {args.interval} 秒")
    log.info(f"搜索半径: {SEARCH_RADIUS} 海里")
    log.info(f"数据目录: {DATA_DIR}")
    log.info(f"{'='*60}")

    if args.dry_run:
        log.info("试跑模式：采集一次并打印")
        total = run_collect_cycle()
        log.info(f"试跑完成，本次采集 {total} 架飞机")
        csv_path = get_today_csv_path()
        if os.path.exists(csv_path):
            log.info(f"数据已写入: {csv_path}")
        return

    if args.once:
        total = run_collect_cycle()
        log.info(f"采集完成，本次采集 {total} 架飞机")
        return

    # 主循环
    log.info("进入主循环...")
    cycle_count = 0
    while True:
        try:
            total = run_collect_cycle()
            cycle_count += 1
            log.info(f"[#{cycle_count}] 采集到 {total} 架飞机 — 等待 {args.interval} 秒")
        except KeyboardInterrupt:
            log.info("用户中断，采集停止")
            break
        except Exception as e:
            log.error(f"采集异常: {e}", exc_info=True)
            log.info(f"等待 {args.interval} 秒后重试...")

        time.sleep(args.interval)

    log.info(f"采集结束，共完成 {cycle_count} 次轮询")


if __name__ == "__main__":
    main()