# 수도권 실거래 전량 수집 — 매매 → 전월세 순.
#
# Claude 앱이나 터미널과 무관하게 돌도록 **작업 스케줄러에서 실행**하는 것을 전제로 한다.
# 터미널에서 직접 돌리면 그 터미널을 닫을 때 같이 죽는다.
#
#   등록:  scripts\register_collect_task.ps1
#   확인:  Get-Content <repo>\logs\collect.log -Tail 20 -Wait
#
# 중간에 끊겨도 안전하다. (거래월 × 시군구) 마다 커밋하고, 다시 돌리면
# collection_log 를 보고 이미 받은 조합을 건너뛴다. 실패한 조합은 다시 받는다.

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "collect.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# 작업 스케줄러 콘솔은 cp949 다. 파이썬이 한글 안내문(em-dash 등)을 찍다가
# UnicodeEncodeError 로 죽은 적이 있다 — 한도 소진을 곱게 처리하고도 exit 1 이 됐다.
$env:PYTHONIOENCODING = utf-8
Set-Location $repo
Write-Log "=== 수집 시작 (PID $PID) ==="

# 매매 — 실패한 조합이 남아 있으면 두 번째 실행이 그것만 다시 받는다.
foreach ($pass in 1, 2) {
    Write-Log "매매 ${pass}차 시작"
    & $python -m apt_engine.cli collect trades --months 240 2>&1 |
        ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }
    Write-Log "매매 ${pass}차 종료 (exit $LASTEXITCODE)"
}

# 전월세 — 같은 이유로 두 번.
foreach ($pass in 1, 2) {
    Write-Log "전월세 ${pass}차 시작"
    & $python -m apt_engine.cli collect rents --months 240 2>&1 |
        ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }
    Write-Log "전월세 ${pass}차 종료 (exit $LASTEXITCODE)"
}

Write-Log "=== 수집 끝. 다음: match -> snapshot -> validate ==="
