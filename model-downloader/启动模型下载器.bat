@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto noenv
start "" /D "%~dp0" ".venv\Scripts\pythonw.exe" "app.py"
exit /b 0
:noenv
echo [错误] 找不到 .venv 运行环境，请在项目目录下重新安装依赖。
pause
exit /b 1
