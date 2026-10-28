@echo off
start "ShareSpeaker" powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0src\HubWindow.ps1"
