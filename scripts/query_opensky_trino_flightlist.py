"""
OpenSky Trino — BTH 多机场航班列表查询脚本
=============================================
功能：查询指定日期范围内 BTH 三机场的离港/到港航班列表
数据来源：opensky.flights_data4 表
输出：每个机场的离港/到港 CSV 文件

使用方法：
  1. 先确保 OpenSky Trino 访问权限已开通
  2. 配置 pyopensky 凭据（settings.conf 或环境变量）
  3. pip install pyopensky pandas
  4. python query_opensky_trino_flightlist.py

配置方式（三选一）：
  A. settings.conf 文件（~/.config/pyopensky/settings.conf）
  B. 环境变量 OPENSKY_USERNAME / OPENSKY_PASSWORD
  C. 浏览器自动登录（首次使用）
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
    # 查询日期范围（UTC 时间）
    "start_date": "2025-01-06",  # YYYY-MM-DD
    "end_date": "2025-01-19",    # YYYY-MM-DD（含）
    # 输出目录
    "output_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs"),
    # 是否压缩缓存
    "compress_cache": False,
    # 是否强制重新查询（忽略缓存）
    "force_refresh": False,
    # 每批次查询天数（Trino 单次查询建议不超过 7 天）
    "batch_days": 7,
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
    将日期范围切分成多个子区间，避免单次 Trino 查询数据量过大。
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield current, chunk_end
        current = chunk_end


def query_flights(trino, airport: str, start_dt, end_dt, direction: str):
    """
    查询单个机场在指定时间范围内的航班。
    
    Parameters:
        trino: pyopensky Trino 实例
        airport: ICAO 机场代码
        start_dt: 开始时间（datetime, UTC）
        end_dt: 结束时间（datetime, UTC）
        direction: "departure" 或 "arrival"
    
    Returns:
        pd.DataFrame | None
    """
    try:
        if direction == "departure":
            df = trino.flightlist(
                start=start_dt,
                stop=end_dt,
                departure_airport=airport,
                cached=not CONFIG["force_refresh"],
                compress=CONFIG["compress_cache"],
            )
        elif direction == "arrival":
            df = trino.flightlist(
                start=start_dt,
                stop=end_dt,
                arrival_airport=airport,
                cached=not CONFIG["force_refresh"],
                compress=CONFIG["compress_cache"],
            )
        else:
            # 同时查询离港和到港
            df = trino.flightlist(
                start=start_dt,
                stop=end_dt,
                airport=airport,
                cached=not CONFIG["force_refresh"],
                compress=CONFIG["compress_cache"],
            )
        return df
    except Exception as e:
        print(f"  ❌ 查询失败 [{airport} {direction} {start_dt.date()}]: {e}")
        return None


def main():
    """主流程：遍历机场和日期范围，逐批查询并保存结果"""
    print("=" * 60)
    print("OpenSky Trino — BTH 多机场航班列表查询")
    print("=" * 60)
    print(f"日期范围: {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    print(f"目标机场: {', '.join(CONFIG['airports'])}")
    print(f"每批天数: {CONFIG['batch_days']}天")
    print(f"输出目录: {CONFIG['output_dir']}")
    print("=" * 60)
    
    # 确保输出目录存在
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    
    # 导入 pyopensky
    Trino = setup_pyopensky()
    
    # 初始化 Trino 连接
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
    
    # 生成日期子区间
    chunks = list(date_range_chunks(
        CONFIG["start_date"], CONFIG["end_date"], CONFIG["batch_days"]
    ))
    print(f"\n共 {len(chunks)} 个批次：")
    for i, (cs, ce) in enumerate(chunks):
        print(f"  批次 {i+1}: {cs.date()} ~ {ce.date() - timedelta(days=1)}")
    
    # 收集所有查询结果（按机场+方向）
    results = {}
    
    for airport in CONFIG["airports"]:
        name_cn = CONFIG["airport_names"].get(airport, airport)
        print(f"\n{'='*50}")
        print(f"📌 {name_cn} ({airport})")
        print(f"{'='*50}")
        
        for direction in ["departure", "arrival"]:
            dir_label = "🛫 离港" if direction == "departure" else "🛬 到港"
            print(f"\n  {dir_label}")
            
            all_frames = []
            total_records = 0
            
            for batch_idx, (chunk_start, chunk_end) in enumerate(chunks):
                print(f"    批次 {batch_idx+1}/{len(chunks)}: {chunk_start.date()} ~ ", end="")
                print(f"{(chunk_end - timedelta(days=1)).date()}", end=" ")
                
                df = query_flights(trino, airport, chunk_start, chunk_end, direction)
                
                if df is not None and len(df) > 0:
                    all_frames.append(df)
                    total_records += len(df)
                    print(f"→ {len(df)} 条记录")
                elif df is not None:
                    print("→ 0 条记录")
                else:
                    print("→ 查询失败")
            
            # 合并所有批次
            if all_frames:
                combined = pd.concat(all_frames, ignore_index=True)
                # 去重
                combined = combined.drop_duplicates()
                key = f"{airport}_{direction}"
                results[key] = combined
                print(f"\n  ✅ {dir_label} 总计: {len(combined)} 条航班记录（去重后）")
            else:
                print(f"\n  ⚠️ {dir_label} 无数据")
    
    # 保存结果
    print(f"\n\n{'='*60}")
    print("保存结果...")
    print(f"{'='*60}")
    
    summary = []
    for key, df in results.items():
        airport, direction = key.split("_")
        name_cn = CONFIG["airport_names"].get(airport, airport)
        
        # CSV 输出
        csv_path = os.path.join(CONFIG["output_dir"], f"flights_{airport}_{direction}.csv")
        df.to_csv(csv_path, index=False)
        print(f"  ✅ {name_cn} {'离港' if direction == 'departure' else '到港'}: {len(df)} 条 → {csv_path}")
        
        summary.append({
            "airport": airport,
            "airport_name": name_cn,
            "direction": "离港" if direction == "departure" else "到港",
            "records": len(df),
            "file": csv_path,
        })
    
    # 生成汇总报告
    print(f"\n{'='*60}")
    print("📊 查询汇总")
    print(f"{'='*60}")
    
    total = 0
    for s in summary:
        print(f"  {s['airport_name']} ({s['airport']}) - {s['direction']}: {s['records']} 条")
        total += s['records']
    
    print(f"\n  🏁 总计: {total} 条航班记录")
    print(f"  日期范围: {CONFIG['start_date']} ~ {CONFIG['end_date']}")
    print(f"\n✅ 全部完成！")


if __name__ == "__main__":
    main()