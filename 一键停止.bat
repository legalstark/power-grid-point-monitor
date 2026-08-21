@echo off
chcp 65001 >nul
taskkill /F /IM "数据模拟器.exe" >nul 2>&1
taskkill /F /IM "测点监视工具.exe" >nul 2>&1
echo 已停止数据模拟器和测点监视工具。
timeout /t 2 /nobreak >nul
