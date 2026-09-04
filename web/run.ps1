$ErrorActionPreference = "Stop"
$proj = Split-Path $PSScriptRoot -Parent
Set-Location $proj
$py = Join-Path $proj ".venv\Scripts\python.exe"
Start-Process "http://127.0.0.1:5088"
& $py (Join-Path $PSScriptRoot "app.py")
