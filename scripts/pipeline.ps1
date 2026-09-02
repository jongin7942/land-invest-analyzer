# 수집 이후 전 과정: 마이그레이션 → 매칭 → 스냅샷 → 검증 → 백테스트.
#
#   등록:  scripts\register_pipeline_task.ps1
#   확인:  Get-Content <repo>\logs\pipeline.log -Tail 30 -Wait
#   현황:  Get-Content <repo>\logs\pipeline.state
#
# ── 끊겨도 이어받는다 ──────────────────────────────────────────────────
# 이 스크립트는 30분마다 다시 뜬다. 끝난 단계는 pipeline.state 에 적어두고
# 다음 실행에서 건너뛴다. 그래서 아래 어느 경우에도 이어서 진행된다:
#
#   * Claude 세션이 한도로 끊길 때   (작업 스케줄러가 띄우므로 애초에 무관)
#   * PC 를 껐다 켤 때               (StartWhenAvailable + 30분 반복)
#   * 한 단계가 죽을 때              (그 단계부터 다시)
#
# 이미 돌고 있으면 새로 뜨지 않는다(MultipleInstances=IgnoreNew). 두 개가 같은
# SQLite 에 긴 쓰기를 하면 서로 죽인다.
#
# ── 왜 한 줄씩 따로 돌리는가 ───────────────────────────────────────────
# 앞 단계가 실패하면 뒤 단계는 의미가 없다. 잘못된 매칭 위에 스냅샷을 만들면
# 틀린 대표가격이 나오고, 그 위에 백테스트를 돌리면 틀린 가중치가 학습된다.

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "pipeline.log"
$state = Join-Path $logDir "pipeline.state"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Log($msg) {
    Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}
function Get-Done {
    if (Test-Path $state) { @(Get-Content $state -Encoding UTF8 | Where-Object { $_ }) } else { @() }
}
function Set-Done($name) { Add-Content -Path $state -Encoding UTF8 -Value $name }

function Get-Busy {
    # 수집이든 매칭이든 이미 DB 에 길게 쓰는 게 있으면 건드리지 않는다.
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*apt_engine.cli*" -and
                       $_.CommandLine -notlike "*apt_app*" })
}

$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo

$busy = Get-Busy
if ($busy.Count -gt 0) {
    Write-Log "이미 다른 apt_engine 작업이 돌고 있어 이번 회차는 건너뜁니다 (PID $($busy.ProcessId -join ', '))."
    return
}

# 백테스트 구간은 **스냅샷이 있는 범위**에서 뽑는다.
#
# 거래 기간(2006~2026)으로 잡았더니 backtest run 이 exit 0 으로 끝나면서도
# 채점된 창이 하나도 없었다. 백테스트는 "그 시점에 이 단지가 얼마였나" 를
# 스냅샷에서 읽는데, 스냅샷이 최근 12개월치뿐이라 과거 창을 평가할 수 없었다.
# 원자료가 있다고 평가할 수 있는 게 아니다 - 스냅샷이 있어야 한다.
#
# 안 주면 run 이 None 을 받아 ValueError 로 죽는다(plan 은 넘어가서 더 헷갈린다).
$DATA_START = & $python -c "import sqlite3,config; c=sqlite3.connect(f'file:{config.APT_DB_PATH}?mode=ro',uri=True); d=c.execute('SELECT MIN(as_of_ym) FROM price_snapshot').fetchone()[0]; print(f'{d[:4]}-{d[4:6]}-01')"
$DATA_END   = & $python -c "import sqlite3,config; c=sqlite3.connect(f'file:{config.APT_DB_PATH}?mode=ro',uri=True); d=c.execute('SELECT MAX(as_of_ym) FROM price_snapshot').fetchone()[0]; print(f'{d[:4]}-{d[4:6]}-01')"
Write-Log "데이터 구간 $DATA_START ~ $DATA_END"

# validate 는 위반이 있으면 0 이 아닌 값을 낸다. 그건 실패가 아니라 결과다.
# backtest plan 도 '표본 부족' 을 0 이 아닌 값으로 알릴 수 있다.
$steps = @(
    @{ N = "init";             A = @("init");                                        Soft = $false }
    @{ N = "match";            A = @("match", "--rebuild");                           Soft = $false }
    @{ N = "snapshot";         A = @("snapshot", "--months", "120", "--window", "6");   Soft = $false }
    @{ N = "validate";         A = @("validate");                                     Soft = $true  }
    @{ N = "backtest-plan";    A = @("backtest", "plan", "--horizon", "2", "--step", "3",
                                     "--start", $DATA_START, "--end", $DATA_END); Soft = $true }
    @{ N = "backtest-run";     A = @("backtest", "run", "--horizon", "2", "--step", "3",
                                     "--start", $DATA_START, "--end", $DATA_END,
                                     "--run-key", "wf1", "--cash", "3", "--purge");    Soft = $false }
    @{ N = "backtest-weights"; A = @("backtest", "weights", "--run-key", "wf1");       Soft = $false }
)

$done = Get-Done
if (($steps | Where-Object { $done -notcontains $_.N }).Count -eq 0) {
    return   # 다 끝났다. 조용히 나간다 — 30분마다 뜨는 작업이라 로그를 더럽히지 않는다.
}

Write-Log "=== 파이프라인 (PID $PID) · 끝난 단계: $($done -join ', ') ==="

foreach ($s in $steps) {
    if ($done -contains $s.N) { continue }

    Write-Log "─── $($s.N) 시작 ───"
    $t0 = Get-Date
    # -u : 파이프에 물리면 파이썬이 stdout 을 버퍼링해서 진행률이 안 보인다.
    #      6시간 동안 로그가 한 줄도 안 나온 적이 있다.
    & $python -u -m apt_engine.cli @($s.A) 2>&1 |
        ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }
    $code = $LASTEXITCODE
    $mins = [int]((Get-Date) - $t0).TotalMinutes
    Write-Log "─── $($s.N) 종료 (exit $code, ${mins}분) ───"

    if ($code -eq 0 -or $s.Soft) {
        Set-Done $s.N
    } else {
        Write-Log "$($s.N) 실패. 30분 뒤 이 단계부터 다시 시도합니다."
        return
    }
}

Write-Log "=== 파이프라인 끝. 순위 화면이 풀렸는지 확인하세요 ==="
