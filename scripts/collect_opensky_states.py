#!/usr/bin/env python3
"""
OpenSky - BTH region ADS-B real-time collector (OAuth2)
=========================================================
Polls OpenSky Network states/all API every 5s for the BTH bounding box.
Data saved to D:/PARTD/data/flights/opensky/, one CSV per day.

用法：
    python collect_opensky_states.py                              # 默认启动
    python collect_opensky_states.py --credentials creds.json     # 指定凭据
    python collect_opensky_states.py --interval 10                # 自定义间隔
    python collect_opensky_states.py --anonymous                  # 匿名模式（10秒）
    python collect_opensky_states.py --dry-run                    # 试跑

依赖：
    pip install requests
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# BTH 区域地理包围框
BTH_BBOX = {
    "lamin": 39.0,   # 南纬
    "lamax": 40.7,   # 北纬
    "lomin": 116.0,  # 西经
    "lomax": 118.0,  # 东经
}

# OAuth2 端点
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API_BASE = "https://opensky-network.org/api"
STATES_URL = f"{API_BASE}/states/all"

INTERVAL_AUTH = 5     # 认证用户：5 秒/次
INTERVAL_ANON = 10    # 匿名用户：10 秒/次

# 输出目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data", "flights", "opensky")

# CSV 字段（对应 OpenSky state vector 数组索引）
STATE_INDEX = {
    "icao24": 0,
    "callsign": 1,
    "origin_country": 2,
    "time_position": 3,
    "last_contact": 4,
    "longitude": 5,
    "latitude": 6,
    "baro_altitude": 7,
    "on_ground": 8,
    "velocity": 9,
    "true_track": 10,
    "vertical_rate": 11,
    "geo_altitude": 13,
    "squawk": 14,
}

CSV_FIELDS = [
    "fetch_ts",          # 采集时间戳 (ISO 8601)
    "icao24",            # ICAO 24位地址
    "callsign",          # 航班号
    "origin_country",    # 所属国家
    "time_position",     # 位置时间戳
    "last_contact",      # 最后联系时间
    "longitude",         # 经度
    "latitude",          # 纬度
    "baro_altitude",     # 气压高度 (米)
    "on_ground",         # 是否在地面
    "velocity",          # 地速 (米/秒)
    "true_track",        # 航向 (度)
    "vertical_rate",     # 垂直速率 (米/秒)
    "geo_altitude",      # 几何高度 (米)
    "squawk",            # 应答机编码
]

# 默认凭据路径
DEFAULT_CREDENTIALS = os.path.join(
    os.path.expanduser("~"), "Downloads", "credentials.json"
)

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] opensky: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("opensky-collector")


# ---------------------------------------------------------------------------
# OAuth2 会话管理器
# ---------------------------------------------------------------------------

class OAuth2Session:
    """
    管理 OpenSky OAuth2 客户端凭证流，自动刷新 Token
    """

    def __init__(self, credentials_path: str):
        with open(credentials_path, "r") as f:
            creds = json.load(f)
        self.client_id = creds["clientId"]
        self.client_secret = creds["clientSecret"]
        self.access_token = None
        self._expires_at = 0.0

    def _fetch_token(self) -> str:
        """向 OpenSky 获取新的 access_token"""
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 1800)  # 默认 30 分钟
        self._expires_at = time.time() + expires_in - 60  # 提前 60 秒刷新
        log.info("OAuth2 token 获取成功，有效期 ~30 分钟")
        return token

    def get_token(self) -> str:
        """获取有效 token，必要时自动刷新"""
        if not self.access_token or time.time() >= self._expires_at:
            log.info("正在获取/刷新 OAuth2 token...")
            self.access_token = self._fetch_token()
        return self.access_token

    def get_auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------

def fetch_states(
    auth_header: Optional[Dict] = None,
) -> Optional[dict]:
    """
    调用 OpenSky /states/all 接口
    返回解析后的 JSON 字典，失败返回 None
    """
    params = {
        "lamin": BTH_BBOX["lamin"],
        "lamax": BTH_BBOX["lamax"],
        "lomin": BTH_BBOX["lomin"],
        "lomax": BTH_BBOX["lomax"],
    }
    headers = {"Content-Type": "application/json"}
    if auth_header:
        headers.update(auth_header)

    try:
        resp = requests.get(STATES_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 401:
            log.warning("Token 过期（401），将在下一轮刷新")
        else:
            log.warning(f"HTTP 错误: {e}")
        return None
    except requests.exceptions.RequestException as e:
        log.warning(f"API 请求失败: {e}")
        return None
    except json.JSONDecodeError as e:
        log.warning(f"JSON 解析失败: {e}")
        return None


def parse_states(data: dict, fetch_ts: str) -> List[Dict]:
    """
    将 OpenSky API 返回的 2D 状态数组解析为 CSV 行列表
    """
    if not data or "states" not in data:
        return []

    seen_icao24 = set()
    rows = []

    for state_arr in data["states"]:
        icao24 = (state_arr[STATE_INDEX["icao24"]] or "").strip().upper()
        if not icao24 or icao24 in seen_icao24:
            continue
        seen_icao24.add(icao24)

        row = {
            "fetch_ts": fetch_ts,
            "icao24": icao24,
            "callsign": (state_arr[STATE_INDEX["callsign"]] or "").strip().upper(),
            "origin_country": (state_arr[STATE_INDEX["origin_country"]] or "").strip(),
            "time_position": state_arr[STATE_INDEX["time_position"]],
            "last_contact": state_arr[STATE_INDEX["last_contact"]],
            "longitude": state_arr[STATE_INDEX["longitude"]],
            "latitude": state_arr[STATE_INDEX["latitude"]],
            "baro_altitude": state_arr[STATE_INDEX["baro_altitude"]],
            "on_ground": 1 if state_arr[STATE_INDEX["on_ground"]] else 0,
            "velocity": state_arr[STATE_INDEX["velocity"]],
            "true_track": state_arr[STATE_INDEX["true_track"]],
            "vertical_rate": state_arr[STATE_INDEX["vertical_rate"]],
            "geo_altitude": state_arr[STATE_INDEX["geo_altitude"]],
            "squawk": str(state_arr[STATE_INDEX["squawk"]] or "").strip(),
        }
        rows.append(row)

    return rows


def get_today_csv_path() -> str:
    """获取今天的数据文件路径"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return os.path.join(DATA_DIR, f"opensky_{today}.csv")


def write_rows(path: str, rows: List[Dict]):
    """批量写入 CSV（自动写表头）"""
    if not rows:
        return
    file_exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def run_collect_cycle(auth_header: Optional[Dict] = None) -> int:
    """
    执行一次采集周期：查询 BTH 区域所有飞机状态，写入 CSV
    返回本次采集到的飞机数量
    """
    data = fetch_states(auth_header)
    if not data:
        return 0

    fetch_ts = datetime.now(timezone.utc).isoformat()
    rows = parse_states(data, fetch_ts)

    if rows:
        csv_path = get_today_csv_path()
        write_rows(csv_path, rows)

    return len(rows)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenSky BTH 区域 ADS-B 实时采集器（OAuth2）"
    )
    parser.add_argument(
        "--credentials", "-c", type=str, default=DEFAULT_CREDENTIALS,
        help=f"OAuth2 credentials.json 路径（默认: {DEFAULT_CREDENTIALS}）"
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=None,
        help="轮询间隔（秒），默认：认证用户5秒 / 匿名10秒"
    )
    parser.add_argument(
        "--anonymous", "-a", action="store_true",
        help="强制匿名模式（不加载凭据）"
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

    # ---- 认证设置 ----
    is_anonymous = args.anonymous
    auth_header = None

    if not is_anonymous:
        cred_path = args.credentials
        if cred_path and os.path.exists(cred_path):
            try:
                session = OAuth2Session(cred_path)
                auth_header = session.get_auth_header()
                log.info(f"✅ OAuth2 认证成功（凭据: {cred_path}）")
                log.info("   速率限制: 5 秒/次")
            except Exception as e:
                log.warning(f"⚠️  OAuth2 加载失败: {e}，降级为匿名访问")
                is_anonymous = True
        else:
            log.info("未找到凭据文件，使用匿名访问")
            is_anonymous = True

    if is_anonymous:
        log.info("   🔓 匿名模式 | 速率限制: 10 秒/次")

    # 间隔
    interval = args.interval
    if interval is None:
        interval = INTERVAL_ANON if is_anonymous else INTERVAL_AUTH

    # ---- 启动信息 ----
    log.info(f"{'='*60}")
    log.info("OpenSky BTH 区域 ADS-B 实时采集器")
    log.info(f"  包围框: lat [{BTH_BBOX['lamin']}, {BTH_BBOX['lamax']}], "
             f"lon [{BTH_BBOX['lomin']}, {BTH_BBOX['lomax']}]")
    log.info(f"  采集间隔: {interval} 秒")
    log.info(f"  数据目录: {DATA_DIR}")
    log.info(f"{'='*60}")

    # ---- 试跑 ----
    if args.dry_run:
        log.info("试跑模式：采集一次")
        total = run_collect_cycle(auth_header)
        log.info(f"试跑完成，本次采集 {total} 架飞机")
        csv_path = get_today_csv_path()
        if os.path.exists(csv_path):
            log.info(f"数据已写入: {csv_path}")
        return

    # ---- 单次采集 ----
    if args.once:
        total = run_collect_cycle(auth_header)
        log.info(f"采集完成，本次采集 {total} 架飞机")
        return

    # ---- 主循环 ----
    log.info("进入主循环...")
    cycle_count = 0
    while True:
        try:
            total = run_collect_cycle(auth_header)
            cycle_count += 1
            log.info(f"[#{cycle_count}] 采集到 {total} 架飞机 — 等待 {interval} 秒")
        except KeyboardInterrupt:
            log.info("用户中断，采集停止")
            break
        except Exception as e:
            log.error(f"采集异常: {e}", exc_info=True)
            log.info(f"等待 {interval} 秒后重试...")

        time.sleep(interval)

    log.info(f"采集结束，共完成 {cycle_count} 次轮询")


if __name__ == "__main__":
    main()