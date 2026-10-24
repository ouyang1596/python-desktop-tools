@echo off
cd /d "%~dp0."
if not exist "launcher\__main__.py" (
  echo 请运行项目里的 start.bat，不要把这个文件复制到其他目录。
  echo 当前目录：%CD%
  pause
  exit /b 1
)
python -m launcher
if errorlevel 1 pause
