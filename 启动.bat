@echo off
chcp 65001 >nul 2>&1
title WoW语音包工坊

REM ============================================================
REM  WoW语音包工坊 —— 启动器
REM  自适应查找可用 Python：优先本目录 runtime，其次系统 python
REM ============================================================

cd /d "%~dp0"
set "PY="

REM 1) 随包的 runtime（打包成 exe 后不存在）
if exist "runtime\Scripts\python.exe" set "PY=runtime\Scripts\python.exe"

REM 2) 同机已装 edge-tts 的环境（MultiTTS-VoiceMaker 的 runtime 就有）
if not defined PY (
  for %%P in (
    "L:\工作流\小工具\MultiTTS-VoiceMaker\runtime\Scripts\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  ) do (
    if not defined PY if exist %%P set "PY=%%~P"
  )
)

REM 3) PATH 上的 python
if not defined PY (
  for /f "delims=" %%I in ('where python 2^>nul') do (
    if not defined PY set "PY=%%I"
  )
)

if not defined PY (
  echo.
  echo   [X] 没找到 Python。
  echo.
  echo   请任选一种方式：
  echo     1. 安装 Python 3.10+ 并勾选 "Add to PATH"
  echo     2. 把 Python 放到本目录 runtime\Scripts\python.exe
  echo.
  pause
  exit /b 1
)

echo.
echo   使用 Python: %PY%
echo   启动中，浏览器会自动打开 http://127.0.0.1:8788
echo   （关掉这个窗口就退出程序）
echo.

REM 依赖自检：本地自定义音色需要 sherpa-onnx，在线音色需要 edge-tts
"%PY%" -c "import sherpa_onnx" >nul 2>&1
if errorlevel 1 (
  echo   正在安装 sherpa-onnx（本地自定义音色需要，约 17MB）...
  "%PY%" -m pip install sherpa-onnx --quiet --disable-pip-version-check
)

"%PY%" -c "import edge_tts" >nul 2>&1
if errorlevel 1 (
  echo   正在安装 edge-tts（在线音色需要）...
  "%PY%" -m pip install edge-tts --quiet --disable-pip-version-check
)

"%PY%" web_app.py

if errorlevel 1 (
  echo.
  echo   程序异常退出 Above。把上面的报错发出来即可排查。
  pause
)
