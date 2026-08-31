#Requires -Version 5.1
# 수집을 Windows 작업 스케줄러에 등록한다.
#
# 왜 굳이 작업 스케줄러인가 —
# 터미널이나 Claude 앱에서 바로 돌리면 그 프로세스의 **자식**이 된다. 앱을 닫으면
# 같이 죽는다. 240개월 수집은 10시간이 넘어서, 그 사이 앱을 못 닫는 건 말이 안 된다.
# 작업 스케줄러가 띄우면 어느 창과도 무관하게 돈다.
#
#   등록 + 즉시 실행:  powershell -ExecutionPolicy Bypass -File scripts\register_collect_task.ps1
#   진행 확인:         Get-Content logs\collect.log -Tail 20 -Wait
#   중지:              schtasks /end /tn apt-collect
#   등록 해제:         schtasks /delete /tn apt-collect /f

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "scripts\collect_all.ps1"
$taskName = "apt-collect"

if (-not (Test-Path $script)) { throw "스크립트를 찾을 수 없습니다: $script" }

# 이미 돌고 있으면 두 개가 동시에 쓰게 된다. SQLite 쓰기 락이 충돌해 수집이 죽는다.
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*apt_engine.cli collect*" }
if ($running) {
    Write-Host "이미 수집이 돌고 있습니다 (PID $($running.ProcessId -join ', '))."
    Write-Host "두 개를 동시에 돌리면 DB 쓰기 락이 충돌해 죽습니다. 먼저 멈추세요:"
    Write-Host "  schtasks /end /tn $taskName"
    return
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
schtasks /create /tn $taskName /f /sc daily /st 00:10 /tr $action | Out-Null
schtasks /run /tn $taskName | Out-Null

Write-Host "등록하고 실행했습니다: $taskName"
Write-Host "진행 확인:  Get-Content `"$repo\logs\collect.log`" -Tail 20 -Wait"
