@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 Python，请安装 Python 3.10 或以上版本。
  pause
  exit /b 1
)
if not exist "..\运行日志" mkdir "..\运行日志"
start "A1数据模拟器-源码" /min cmd /c "python simulator_app.py ^> "..\运行日志\数据模拟器-源码.log" 2^>^&1"
timeout /t 2 /nobreak >nul
start "A1测点监视-源码" /min cmd /c "python monitor_app.py --data-dir ..\data ^> "..\运行日志\测点监视工具-源码.log" 2^>^&1"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:9010"
endlocal
