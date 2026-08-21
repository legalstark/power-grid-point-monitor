@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "可执行程序\数据模拟器.exe" (
  echo [错误] 未找到 可执行程序\数据模拟器.exe
  pause
  exit /b 1
)
if not exist "可执行程序\测点监视工具.exe" (
  echo [错误] 未找到 可执行程序\测点监视工具.exe
  pause
  exit /b 1
)
if not exist "运行日志" mkdir "运行日志"
start "A1数据模拟器" /min cmd /c ""可执行程序\数据模拟器.exe" ^> "运行日志\数据模拟器.log" 2^>^&1"
timeout /t 2 /nobreak >nul
start "A1测点监视" /min cmd /c ""可执行程序\测点监视工具.exe" --data-dir data ^> "运行日志\测点监视工具.log" 2^>^&1"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:9010"
echo 已启动数据模拟器和测点监视工具。
echo 浏览器地址：http://127.0.0.1:9010
endlocal
