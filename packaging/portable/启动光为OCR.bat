@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "%~dp0GuangWeiOCR.exe" (
    start "" "%~dp0GuangWeiOCR.exe"
    exit /b 0
)
if exist "%~dp0scoresight.exe" (
    start "" "%~dp0scoresight.exe"
    exit /b 0
)
echo GuangWeiOCR.exe was not found next to this script.
echo Download the portable package again from https://ocr.4018.cn/
pause
exit /b 1
