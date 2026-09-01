# 수집이 끝나면 이어서 돌리는 후처리: match -> snapshot -> validate.
#
# 왜 별도 스크립트인가 —
# match/snapshot 은 **긴 쓰기 트랜잭션**이라 수집과 같이 돌면 서로 죽인다.
# (실제로 수집 중 snapshot 을 돌려 매매 수집을 통째로 날린 적이 있다.)
# 그래서 수집 프로세스가 완전히 사라진 것을 확인한 뒤에 시작한다.
#
#   등록:  scripts\register_postprocess_task.ps1
#   확인:  Get-Content <repo>\logs\postprocess.log -Tail 30 -Wait

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "postprocess.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Log($msg) {
    Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}

function Get-CollectProcs {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*apt_engine.cli collect*" })
}

$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo
Write-Log "=== 후처리 대기 시작 (PID $PID) ==="

# 수집이 끝날 때까지 기다린다. 최대 12시간.
$waited = 0
while ((Get-CollectProcs).Count -gt 0 -and $waited -lt 43200) {
    Start-Sleep -Seconds 30
    $waited += 30
    if ($waited % 600 -eq 0) { Write-Log "  수집 대기 중... ($([int]($waited/60))분)" }
}
if ((Get-CollectProcs).Count -gt 0) {
    Write-Log "12시간을 기다렸는데도 수집이 안 끝났습니다. 후처리를 건너뜁니다."
    return
}
Write-Log "수집 종료 확인. 후처리 시작."

# 한 단계라도 실패하면 그 뒤는 의미가 없다. 중단하고 남긴다.
$steps = @(
    @{ Name = "match";    Args = @("match", "--rebuild") },
    @{ Name = "snapshot"; Args = @("snapshot", "--months", "12", "--window", "6") },
    @{ Name = "validate"; Args = @("validate") }
)
foreach ($s in $steps) {
    Write-Log "--- $($s.Name) 시작 ---"
    & $python -m apt_engine.cli @($s.Args) 2>&1 |
        ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }
    $code = $LASTEXITCODE
    Write-Log "--- $($s.Name) 종료 (exit $code) ---"
    # validate 는 위반이 있으면 0 이 아닌 값을 낸다. 그건 실패가 아니라 결과다.
    if ($code -ne 0 -and $s.Name -ne "validate") {
        Write-Log "$($s.Name) 이 실패했습니다. 뒤 단계를 건너뜁니다."
        return
    }
}

Write-Log "=== 후처리 끝. 다음: backtest plan -> run -> weights ==="
