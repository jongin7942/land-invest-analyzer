# 수집 이후 전 과정: 마이그레이션 → 매칭 → 스냅샷 → 검증 → 백테스트.
#
# 사람이 자리를 비워도 끝까지 가도록 **작업 스케줄러에서** 돌린다.
#
#   등록:  scripts\register_pipeline_task.ps1
#   확인:  Get-Content <repo>\logs\pipeline.log -Tail 30 -Wait
#
# ── 왜 한 줄씩 따로 돌리는가 ───────────────────────────────────────────
# 앞 단계가 실패하면 뒤 단계는 의미가 없다. 잘못된 매칭 위에 스냅샷을 만들면
# 틀린 대표가격이 나오고, 그 위에 백테스트를 돌리면 틀린 가중치가 학습된다.
# 실패하면 거기서 멈추고 로그에 남긴다.
#
# ── 수집과 절대 같이 돌리지 않는다 ─────────────────────────────────────
# match·snapshot 은 긴 쓰기 트랜잭션이라 수집과 겹치면 서로 죽인다.
# 시작 전에 수집 프로세스가 없는지 확인하고, 있으면 기다린다.

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "pipeline.log"

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
Write-Log "=== 파이프라인 시작 (PID $PID) ==="

# 수집이 돌고 있으면 기다린다. 최대 6시간.
$waited = 0
while ((Get-CollectProcs).Count -gt 0 -and $waited -lt 21600) {
    if ($waited % 600 -eq 0) { Write-Log "  수집이 도는 중 - 기다립니다 ($([int]($waited/60))분)" }
    Start-Sleep -Seconds 30; $waited += 30
}
if ((Get-CollectProcs).Count -gt 0) { Write-Log "수집이 안 끝나 파이프라인을 건너뜁니다."; return }

# validate 는 위반이 있으면 0 이 아닌 값을 낸다. 그건 실패가 아니라 결과다.
# backtest plan 도 '부족' 을 0 이 아닌 값으로 알릴 수 있다.
$steps = @(
    @{ N = "init";            A = @("init");                                       Soft = $false }
    @{ N = "match";           A = @("match", "--rebuild");                          Soft = $false }
    @{ N = "snapshot";        A = @("snapshot", "--months", "12", "--window", "6"); Soft = $false }
    @{ N = "validate";        A = @("validate");                                    Soft = $true  }
    @{ N = "backtest plan";   A = @("backtest", "plan", "--horizon", "2", "--step", "3");        Soft = $true }
    @{ N = "backtest run";    A = @("backtest", "run", "--horizon", "2", "--step", "3",
                                    "--run-key", "wf1", "--cash", "3");             Soft = $false }
    @{ N = "backtest weights";A = @("backtest", "weights", "--run-key", "wf1");     Soft = $false }
)

foreach ($s in $steps) {
    Write-Log "─── $($s.N) 시작 ───"
    $t0 = Get-Date
    & $python -m apt_engine.cli @($s.A) 2>&1 |
        ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }
    $code = $LASTEXITCODE
    $mins = [int]((Get-Date) - $t0).TotalMinutes
    Write-Log "─── $($s.N) 종료 (exit $code, ${mins}분) ───"
    if ($code -ne 0 -and -not $s.Soft) {
        Write-Log "$($s.N) 이 실패했습니다. 뒤 단계를 건너뜁니다."
        Write-Log "=== 파이프라인 중단 ==="
        return
    }
}

Write-Log "=== 파이프라인 끝. 순위 화면이 풀렸는지 확인하세요 ==="
