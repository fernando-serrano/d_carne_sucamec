@echo off
cd /d %~dp0
set PYTHONPATH=%~dp0src
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m carnet_flow %*
