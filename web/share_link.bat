@echo off
chcp 65001 >nul
title 아파트 투자후보 공유링크
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoLogo -ExecutionPolicy Bypass -File "C:\Users\jongi\land-invest-analyzer\web\share_link.ps1"
) else (
  powershell -NoLogo -ExecutionPolicy Bypass -File "C:\Users\jongi\land-invest-analyzer\web\share_link.ps1"
)
if errorlevel 1 pause
