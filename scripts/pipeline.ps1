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
#
# **읽는 시점이 중요하다.** 예전에는 스크립트 맨 위에서 한 번만 읽었다. 그런데
# 같은 실행 안에서 snapshot 단계가 범위를 바꾼다 - 120개월을 240개월로 늘렸더니
# 스냅샷은 2006-10 부터 생겼는데 백테스트는 이미 읽어둔 2016-10 으로 돌았다.
# 창이 17개밖에 안 나왔고 VALIDATION 표본이 모자라 가중치가 0개였다.
# 그래서 자리표시자를 넣어두고 백테스트 단계 **직전에** 다시 읽는다.
function Get-DataRange {
    $a = & $python -c "import sqlite3,config; c=sqlite3.connect(f'file:{config.APT_DB_PATH}?mode=ro',uri=True); d=c.execute('SELECT MIN(as_of_ym) FROM price_snapshot').fetchone()[0]; print(f'{d[:4]}-{d[4:6]}-01')"
    $b = & $python -c "import sqlite3,config; c=sqlite3.connect(f'file:{config.APT_DB_PATH}?mode=ro',uri=True); d=c.execute('SELECT MAX(as_of_ym) FROM price_snapshot').fetchone()[0]; print(f'{d[:4]}-{d[4:6]}-01')"
    return @($a, $b)
}

# ── 보유기간 1년 · 창 간격 6개월 · 스냅샷 240개월 ──────────────────
# 첫 판에서 가중치가 0개 학습됐다. feature 마다 사유가 같았다 —
#   TRAIN 창 14개 중 겹치지 않는 것 2개, VALIDATION 7개 중 1개 (최소 3개).
# 겹친 창을 따로 세면 표본을 부풀리는 것이라 usefulness 가 거부한 것이고 옳다.
#
# 데이터가 모자란 게 아니라 우리가 쓴 구간이 짧았다. 매매 원본은 2006-09 부터
# 있는데 스냅샷을 120개월만 만들었다. 20년이 있는데 10년만 쓰고 있었다.
#
# 보유기간 2년은 240개월로도 안 된다 - 창이 24개월씩 떨어져야 하는데 분할 하나가
# 그만큼 길지 않다. 2년 가중치를 배우려면 30년어치가 필요하다. 1년으로 내리면
# VALIDATION 이 약 60개월 -> 겹치지 않는 창 5개가 된다.
#
# 창 간격을 3->6개월로 늘렸다. 겹치지 않는 창 개수는 분할의 길이로 정해지지
# 간격으로 정해지지 않는다. 간격을 좁혀봐야 계산량만 두 배가 된다.

# ── 백테스트는 --no-loan 로 돌린다 ─────────────────────────────────
# 기본(STRICT) 게이트는 실투자금(세금 + 부대비용 - 대출 - 승계전세)으로 거른다.
# 과거 창에서 이게 성립하려면 규칙이 그 시점에 있어야 하는데, 하나씩 막혔다.
# 실측으로 확인한 순서가 그대로 기록이다 (전부 '매수가능 0개'):
#
#   실투자금 확인 불가: 취득세          → rules/tax_history.csv (2013-08-28~)
#   실투자금 확인 불가: 중개보수         → rules/cost_history.csv (서울·경기·인천)
#   실투자금 확인 불가: 중개보수 부가세   → Profile.agent_vat_registered
#   실투자금 확인 불가: 인지세·국민주택채권 → rules/registration_costs.csv
#   실투자금 확인 불가: 증명서발급        → rules/certificate_costs.csv
#   실투자금 확인 불가: 대출 가능액       → 여기서 멈췄다
#
# loan_rule 은 2025-10-16 부터만 있다. LTV·DSR 연혁을 채우려면 규제지역 연혁이
# 먼저 필요한데(LTV 가 조정 여부로 갈린다), 고시가 2016~2023 에 열 번 넘게
# 바뀌어서 하루에 안전하게 끝낼 분량이 아니다.
#
# --price-only 로 내려가면 세금과 부대비용을 통째로 안 센다. 10억짜리에서
# 취득세 3천만 + 중개보수 500만을 빼먹는 것이라 필요현금이 크게 어긋난다.
# --no-loan 은 그 중간이다 - 세금·부대비용은 다 세고 대출만 0 으로 본다.
# 필요현금을 크게 잡는 방향이라 못 사는 집이 올라오지 않는다.
#
# 가중치 학습에는 이걸로 충분하다. 자본 게이트는 후보 풀을 고르는 자리이고,
# 학습은 그 풀 안에서 무엇이 올랐는지를 본다. 대출 규칙이 채워지면 STRICT 로
# 다시 돌린다. 현금 10억은 후보를 충분히 남기려는 값이다.

# validate 는 위반이 있으면 0 이 아닌 값을 낸다. 그건 실패가 아니라 결과다.
# backtest plan 도 '표본 부족' 을 0 이 아닌 값으로 알릴 수 있다.
$steps = @(
    @{ N = "init";             A = @("init");                                        Soft = $false }
    @{ N = "match";            A = @("match", "--rebuild");                           Soft = $false }
    @{ N = "snapshot";         A = @("snapshot", "--months", "240", "--window", "6");   Soft = $false }
    @{ N = "validate";         A = @("validate");                                     Soft = $true  }
    @{ N = "backtest-plan";    A = @("backtest", "plan", "--horizon", "1", "--step", "6",
                                     "--start", "<DATA_START>", "--end", "<DATA_END>"); Soft = $true }
    @{ N = "backtest-run";     A = @("backtest", "run", "--horizon", "1", "--step", "6",
                                     "--start", "<DATA_START>", "--end", "<DATA_END>",
                                     "--run-key", "wf1", "--cash", "10",
                                     "--no-loan", "--purge");                     Soft = $false }
    @{ N = "backtest-weights"; A = @("backtest", "weights", "--run-key", "wf1");       Soft = $false }
)

$done = Get-Done
if (($steps | Where-Object { $done -notcontains $_.N }).Count -eq 0) {
    return   # 다 끝났다. 조용히 나간다 — 30분마다 뜨는 작업이라 로그를 더럽히지 않는다.
}

Write-Log "=== 파이프라인 (PID $PID) · 끝난 단계: $($done -join ', ') ==="

foreach ($s in $steps) {
    if ($done -contains $s.N) { continue }

    # 자리표시자를 지금 채운다 — snapshot 단계가 범위를 바꿔놨을 수 있다.
    $stepArgs = @($s.A)
    if ($stepArgs -contains "<DATA_START>") {
        $range = Get-DataRange
        Write-Log "데이터 구간 $($range[0]) ~ $($range[1])"
        $stepArgs = $stepArgs | ForEach-Object {
            $_ -replace "<DATA_START>", $range[0] -replace "<DATA_END>", $range[1] }
    }

    Write-Log "─── $($s.N) 시작 ───"
    $t0 = Get-Date
    # -u : 파이프에 물리면 파이썬이 stdout 을 버퍼링해서 진행률이 안 보인다.
    #      6시간 동안 로그가 한 줄도 안 나온 적이 있다.
    & $python -u -m apt_engine.cli @($stepArgs) 2>&1 |
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
