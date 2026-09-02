# 파이프라인 감시견 — 15분마다 "진짜로 진행 중인가" 를 확인하고, 멈췄으면 되살린다.
#
#   등록:  scripts\register_watchdog_task.ps1
#   확인:  Get-Content <repo>\logs\status.txt
#   기록:  Get-Content <repo>\logs\watchdog.log -Tail 30
#
# ── 왜 필요한가 ────────────────────────────────────────────────────────
# apt-pipeline 은 30분마다 뜨지만 `MultipleInstances=IgnoreNew` 라, 앞 회차가
# **살아만 있으면** 새 회차가 그냥 물러난다. 그런데 프로세스가 살아서 CPU 를 태우며
# 아무 진전도 못 내는 경우가 실제로 있었다:
#
#   * 매칭이 인덱스를 못 써서 11억 행을 훑던 때 — 7시간 동안 커밋 0회
#   * 파이썬 stdout 버퍼링으로 로그가 한 줄도 안 나오던 때
#
# 그래서 "프로세스가 있다" 가 아니라 **결과물이 늘었는가** 로 판단한다.
# 두 번 연속(30분) 아무것도 안 늘면 멈춘 것으로 보고 죽인다. 그러면 다음 회차의
# apt-pipeline 이 끝난 단계를 건너뛰고 그 자리에서 이어받는다.

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "watchdog.log"
$mark = Join-Path $logDir "watchdog.mark"     # 직전 관측값
$status = Join-Path $logDir "status.txt"      # 사람이 읽는 현재 상태

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
function Write-Log($m) {
    Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format "MM-dd HH:mm:ss"), $m)
}

$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo

$ALL = @("init","match","snapshot","validate","backtest-plan","backtest-run","backtest-weights")
$done = if (Test-Path "$logDir\pipeline.state") {
    @(Get-Content "$logDir\pipeline.state" -Encoding UTF8 | Where-Object { $_ })
} else { @() }
$left = @($ALL | Where-Object { $done -notcontains $_ })

# ── 진행 지표: 결과물 개수. 프로세스 생사가 아니라 이게 늘어야 진행이다 ──
$counts = & $python -c @"
import sqlite3, config
c = sqlite3.connect(f'file:{config.APT_DB_PATH}?mode=ro', uri=True)
q = lambda s: c.execute(s).fetchone()[0]
print(q('SELECT COUNT(*) FROM price_snapshot'),
      q('SELECT COUNT(complex_id) FROM trade'),
      q('SELECT COUNT(complex_id) FROM jeonse_contract'))
"@ 2>$null
$logLen = if (Test-Path "$logDir\pipeline.log") { (Get-Item "$logDir\pipeline.log").Length } else { 0 }
$now = "$counts $logLen"

$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*apt_engine.cli*" -and $_.CommandLine -notlike "*apt_app*" })

# ── 사람이 읽는 상태 파일 ──
$lines = @(
    "확인 시각 : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "끝난 단계 : $(if ($done) { $done -join ' -> ' } else { '없음' })",
    "남은 단계 : $(if ($left) { $left -join ', ' } else { '없음 (전부 완료)' })",
    "실행 중   : $(if ($procs) { ($procs | ForEach-Object { ($_.CommandLine -replace '.*cli ','').Trim() }) -join ', ' } else { '없음' })",
    "진행 지표 : 스냅샷·매칭·로그 = $now"
)

if ($left.Count -eq 0) {
    $lines += "상태      : 전부 끝났습니다. 순위 화면이 풀렸는지 확인하세요."
    Set-Content -Path $status -Value $lines -Encoding UTF8
    return
}

$prev = if (Test-Path $mark) { Get-Content $mark -Raw -Encoding UTF8 } else { "" }
$stalledCount = 0
if (Test-Path "$mark.count") { $stalledCount = [int](Get-Content "$mark.count" -Raw) }

if ($procs.Count -eq 0) {
    # 아무것도 안 돌고 있는데 남은 단계가 있다 → 바로 깨운다.
    $lines += "상태      : 멈춰 있어서 다시 시작했습니다."
    Write-Log "실행 중인 것이 없고 남은 단계 $($left -join ', ') → apt-pipeline 시작"
    schtasks /run /tn apt-pipeline | Out-Null
    Set-Content -Path "$mark.count" -Value 0
}
elseif ($now.Trim() -eq $prev.Trim()) {
    $stalledCount++
    Set-Content -Path "$mark.count" -Value $stalledCount
    if ($stalledCount -ge 2) {
        # 30분(15분 × 2) 동안 결과물이 하나도 안 늘었다. 살아 있어도 멈춘 것이다.
        $lines += "상태      : 30분째 진전이 없어 중단하고 다시 시작했습니다."
        Write-Log "30분째 진전 없음 (지표 $now). 프로세스 종료 후 재시작."
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 10
        schtasks /run /tn apt-pipeline | Out-Null
        Set-Content -Path "$mark.count" -Value 0
    } else {
        $lines += "상태      : 15분째 진전 없음. 한 번 더 보고 판단합니다."
        Write-Log "진전 없음 1회 (지표 $now)"
    }
} else {
    Set-Content -Path "$mark.count" -Value 0
    $lines += "상태      : 정상 진행 중"
}

Set-Content -Path $mark -Value $now -Encoding UTF8
Set-Content -Path $status -Value $lines -Encoding UTF8
