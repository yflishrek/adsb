@echo off
title BTH ADS-B 双采集器
echo ============================================================
echo   BTH ADS-B 双采集器启动
echo   采集周期：2026-10-09 ~ 2026-10-22（14天）
echo ============================================================
echo.

REM 启动 adsb.lol 采集器（新窗口）
start "adsb.lol 采集器" cmd /c "cd /d D:\PARTD && "C:\Users\commi\.workbuddy\binaries\python\envs\default\Scripts\python" scripts/collect_adsblol.py"

REM 等待 2 秒
timeout /t 2 /nobreak >nul

REM 启动 OpenSky 采集器（新窗口）
start "OpenSky 采集器" cmd /c "cd /d D:\PARTD && "C:\Users\commi\.workbuddy\binaries\python\envs\default\Scripts\python" scripts/collect_opensky_states.py"

echo.
echo ✅ 双采集器已启动！
echo 请查看两个新窗口的日志。
echo 按任意键关闭本窗口（采集器会继续运行）。
pause >nul