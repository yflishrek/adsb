"""
OpenSky Trino — BTH 区域航班轨迹数据查询脚本
===============================================
功能：查询指定日期范围内 BTH 区域飞机状态向量（轨迹）数据
数据来源：opensky.state_vectors_data4 表
输出：每个机场的航班轨迹 CSV 文件（用于 INM/AEDT 噪声建模）

说明：
  - flightlist() 查询航班起降列表（轻量）
  - history()   查询完整轨迹数据（state_vectors_data4，含位置/高度/速度）

使用方法：
  同 query_opensky_trino_flightlist.py
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import pandas as pd

# ============================================================
# 配置区
# ============================================================
CONFIG = {
    # BTH 三机场 ICAO 代码
    "airports": ["ZBAA", "ZBAD", "ZBTJ"],
    "airport_names": {
        "ZBAA": "北京首都",
        "ZBAD": "北京大兴",
        "ZBTJ": "天津滨海",
    },
    # 地理边界（BTH 区域，west, south, east, north）
    # 覆盖三机场进近和离场航线
    "bounds": (115.5, 38.5, 119.0, 41.0),
    # 查询日期范围（UTC 时间）
    "start_date": "2025-01-06",
    "end_date": "2025-01-19",
    # 输出目录
    "output_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs"),
    # 缓存设置
    "compress_cache": False,
    "force_refresh": False,
    # 每批次查询天数（建议历史数据按天分批）
    "batch_days": 1,
}


def setup_pyopensky():
    """尝试导入 pyopensky，如果未安装则给出安装提示"""
    try:
        from pyopensky.trino import Trino
        return Trino
    except ImportError:
        print("=" * 60)
        print("需要安装 pyopensky 库。请运行：")
        print("  pip install pyopensky")
        print("=" * 60)
        sys.exit(1)


def date_range_chunks(start_date: str, end_date: str, chunk_days: int):
    """
    将日期范围切分成多个子区间。
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield current, chunk_end
        current = chunk_end


def query_flightlist(trino, airport: str, start_dt, end_dt, direction: str = None):
    """
    查询航班起降列表（lightweight）。
    """
    kwargs = dict(
        start=start_dt,
        stop=end_dt,
        cached=not CONFIG["force_refresh"],
        compress=CONFIG["compress_cache"],
    )
    
    if direction == "departure":
        kwargs["departure_airport"] = airport
    elif direction == "arrival":
        kwargs["arrival_airport"] = airport
    else:
        kwargs["airport"] = airport
    
    try:
        return trino.flightlist(**kwargs)
    except Exception as e:
        print(f"    ❌ flightlist 查询失败: {e}")
        return None


def query_history(trino, airport: str, start_dt, end_dt, direction: str = None):
    """
    查询航班轨迹数据（state_vectors_data4）。
    获取飞机的经度、纬度、高度、速度、航向等状态向量。
    这是噪声建模的原始数据输入。
    """
    kwargs = dict(
        start=start_dt,
        stop=end_dt,
        cached=not CONFIG["force_refresh"],
        compress=CONFIG["compress_cache"],
    )
    
    # 使用地理边界过滤 BTH 区域
    kwargs["bounds"] = CONFIG["bounds"]
    
    # 按机场过滤
    if direction == "departure":
        kwargs["departure_airport"] = airport
    elif direction == "arrival":
        kwargs["arrival_airport"] = airport
    else:
        kwargs["airport"] = airport
    
    try:
        return trino.history(**kwargs)
    except Exception as e:
        print(f"    ❌ history 查询失败: {e}")
        return None


def main():
    """主流程"""
    print("=" * 60)
    print("OpenSky Trino — BTH 区域航班轨迹数据查询")
    print("  用于 INM/AEDT 噪声建模输入")
    print("=" * 60)
    print(f"日期范围: {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    print(f"目标区域: BTH 多机场区域")
    print(f"地理边界: {CONFIG['bounds']}")
    print(f"输出目录: {CONFIG['output_dir']}")
    print("=" * 60)
    
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    
    # 导入 pyopensky
    Trino = setup_pyopensky()
    
    # 连接 Trino
    print("\n正在连接 OpenSky Trino 数据库...")
    try:
        trino = Trino()
        print("✅ Trino 连接成功！")
    except Exception as e:
        print(f"❌ Trino 连接失败: {e}")
        print("\n请检查凭据配置：")
        print("  1. settings.conf 文件 (~/.config/pyopensky/settings.conf)")
        print("  2. 或环境变量 OPENSKY_USERNAME / OPENSKY_PASSWORD")
        sys.exit(1)
    
    chunks = list(date_range_chunks(CONFIG["start_date"], CONFIG["end_date"], CONFIG["batch_days"]))
    print(f"\n共 {len(chunks)} 个批次（每批 {CONFIG['batch_days']} 天）")
    
    # ============================
    # 第1阶段：查询航班列表（flightlist）
    # ============================
    print(f"\n{'='*60}")
    print("第1阶段：航班列表查询（flightlist）")
    print("="*60)
    
    flight_summary = {}
    
    for airport in CONFIG["airports"]:
        name_cn = CONFIG["airport_names"].get(airport, airport)
        print(f"\n📌 {name_cn} ({airport})")
        
        for direction in ["departure", "arrival"]:
            dir_label = "离港" if direction == "departure" else "到港"
            print(f"  🛫/{dir_label}")
            
            all_frames = []
            
            for batch_idx, (cs, ce) in enumerate(chunks):
                print(f"    批次 {batch_idx+1}/{len(chunks)}: {cs.date()}", end=" ")
                df = query_flightlist(trino, airport, cs, ce, direction)
                if df is not None and len(df) > 0:
                    all_frames.append(df)
                    print(f"→ {len(df)} 条")
                elif df is not None:
                    print("→ 0 条")
                else:
                    print("→ 失败")
            
            if all_frames:
                combined = pd.concat(all_frames, ignore_index=True).drop_duplicates()
                key = f"{airport}_{direction}"
                flight_summary[key] = combined
                csv_path = os.path.join(CONFIG["output_dir"], f"flightlist_{airport}_{direction}.csv")
                combined.to_csv(csv_path, index=False)
                print(f"  ✅ {dir_label} 总计: {len(combined)} 条 → {csv_path}")
    
    # ============================
    # 第2阶段：查询轨迹数据（history）
    # ============================
    print(f"\n{'='*60}")
    print("第2阶段：航班轨迹数据查询（history — state_vectors_data4）")
    print("  包含：经纬度、高度、速度、航向等状态向量")
    print("  注：数据量较大，每个航班有多个时间点采样")
    print("="*60)
    
    for airport in CONFIG["airports"]:
        name_cn = CONFIG["airport_names"].get(airport, airport)
        print(f"\n📌 {name_cn} ({airport})")
        
        for direction in ["departure", "arrival"]:
            dir_label = "离港" if direction == "departure" else "到港"
            print(f"  🛫/{dir_label}")
            
            all_frames = []
            
            for batch_idx, (cs, ce) in enumerate(chunks):
                print(f"    批次 {batch_idx+1}/{len(chunks)}: {cs.date()}", end=" ")
                df = query_history(trino, airport, cs, ce, direction)
                if df is not None and len(df) > 0:
                    all_frames.append(df)
                    print(f"→ {len(df)} 个状态向量点")
                elif df is not None:
                    print("→ 0 条")
                else:
                    print("→ 失败")
            
            if all_frames:
                combined = pd.concat(all_frames, ignore_index=True).drop_duplicates()
                key = f"{airport}_{direction}"
                csv_path = os.path.join(CONFIG["output_dir"], f"trajectory_{airport}_{direction}.csv")
                combined.to_csv(csv_path, index=False)
                print(f"  ✅ {dir_label} 总计: {len(combined)} 个状态向量点 → {csv_path}")
    
    # ============================
    # 汇总
    # ============================
    print(f"\n{'='*60}")
    print("📊 查询汇总")
    print("="*60)
    print(f"  日期范围: {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    print(f"  地理边界: {CONFIG['bounds']}")
    print(f"\n  输出文件（D:/PARTD/outputs/）：")
    print(f"    flightlist_*_departure.csv — 离港航班列表")
    print(f"    flightlist_*_arrival.csv    — 到港航班列表")
    print(f"    trajectory_*_departure.csv   — 离港轨迹数据")
    print(f"    trajectory_*_arrival.csv      — 到港轨迹数据")
    print(f"\n✅ 全部完成！")


if __name__ == "__main__":
    main()