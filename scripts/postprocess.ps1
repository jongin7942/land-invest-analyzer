# 수집이 끝나면 이어서 돌리는 후처리.
#
# 왜 별도 스크립트인가 —
# match/snapshot 은 **긴 쓰기 트랜잭션**이라 수집과 같이 돌면 서로 죽인다.
# (실제로 수집 중 snapshot 을 돌려 매매 수집을 통째로 날린 적이 있다.)
# 그래서 수집 프로세스가 완전히 사라진 것을 확인한 뒤에 시작한다.
#
# ⚠ **"수집 프로세스가 사라졌다" 는 "240개월이 확보됐다" 가 아니다.**
#
#   수집은 일일 한도 소진·API 오류·중단으로 언제든 도중에 끝난다. 그런데
#   프로세스는 정상 종료하고, 이 스크립트는 그걸 완료로 읽어 왔다.
#   그래서 아래에 **품질 게이트**를 뒀다:
#
#     1~7단계  수집 완성도·품질 검사 → 리포트
#     8~10단계 투자점수·백테스트·로직 보정
#
#   8~10 은 1~7 이 통과해야만 돈다. 덜 찬 데이터로 점수를 내면
#   그 점수가 '확정값' 처럼 저장되고, 나중에 데이터가 채워져도
#   누구도 그 숫자를 다시 의심하지 않는다.
#
#   등록:  scripts\register_postprocess_task.ps1
#   확인:  Get-Content <repo>\logs\postprocess.log -Tail 30 -Wait
#   리포트: <repo>\logs\quality_report.txt

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "postprocess.log"
$report = Join-Path $logDir "quality_report.txt"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Log($msg) {
    Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}

# 표준출력을 로그와 품질 리포트 양쪽에 남긴다.
function Invoke-Step($name, $cliArgs, [switch]$IntoReport) {
    Write-Log "--- $name 시작 ---"
    if ($IntoReport) {
        Add-Content -Path $report -Encoding UTF8 -Value "`n===== $name =====`n"
    }
    & $python -m apt_engine.cli @cliArgs 2>&1 | ForEach-Object {
        Add-Content -Path $log -Value $_ -Encoding UTF8
        if ($IntoReport) { Add-Content -Path $report -Value $_ -Encoding UTF8 }
    }
    $code = $LASTEXITCODE
    Write-Log "--- $name 종료 (exit $code) ---"
    return $code
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
Write-Log "수집 프로세스 종료 확인 (완료 여부는 아래 게이트가 판단합니다)."

Set-Content -Path $report -Encoding UTF8 -Value `
    ("데이터 품질 리포트 — {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))

# ── 1~4·6단계 · 수집 완성도 ──────────────────────────────────────────
#   1. 매매 240개월 완전성
#   2. 전월세 240개월 완전성
#   3. 지역·월별 결측률
#   4. 비정상 0건 구간 탐지
#   6. 행정구역 개편 전후 코드 연결
# 넷을 한 번에 본다 — 같은 격자를 세 번 훑을 이유가 없다.
$coverage = Invoke-Step "1~4·6 수집 완성도 (coverage)" @("coverage", "--months", "240") -IntoReport
$collectionComplete = ($coverage -eq 0)

# ── 준비 단계 ────────────────────────────────────────────────────────
# 덜 찬 데이터로도 돌려 둔다. 멱등이고, 다음 수집분이 들어오면 다시 돌린다.
# 여기서 막으면 무엇이 얼마나 비었는지조차 볼 수 없다.
foreach ($s in @(
    @{ Name = "match";    Args = @("match", "--rebuild") },
    @{ Name = "snapshot"; Args = @("snapshot", "--months", "12", "--window", "6") }
)) {
    if ((Invoke-Step $s.Name $s.Args) -ne 0) {
        Write-Log "$($s.Name) 이 실패했습니다. 뒤 단계를 건너뜁니다."
        return
    }
}

# ── 5단계 · 중복·취소거래·이상치 ─────────────────────────────────────
# validate 는 위반이 있으면 0 이 아닌 값을 낸다. 그건 실패가 아니라 결과다.
$validate = Invoke-Step "5 중복·취소·이상치 (validate)" @("validate") -IntoReport

# ── 3단계 보조 · 단지 결측 ───────────────────────────────────────────
Invoke-Step "3 미매칭 단지" @("report", "unmatched", "--limit", "20") -IntoReport | Out-Null

# ── 7단계 · 품질 리포트 ──────────────────────────────────────────────
Write-Log "품질 리포트: $report"

# ── 게이트 ───────────────────────────────────────────────────────────
if (-not $collectionComplete) {
    Write-Log ""
    Write-Log "=== 수집이 아직 끝나지 않았습니다 ==="
    Write-Log "  수집 프로세스는 종료됐지만 240개월 격자가 다 차지 않았습니다."
    Write-Log "  8~10단계(투자점수·백테스트·로직 보정)를 건너뜁니다."
    Write-Log "  무엇이 비었는지: $report"
    Write-Log ""
    Write-Log "  이어받기 (받은 달은 건너뜁니다):"
    Write-Log "    python -m apt_engine.cli collect trades --months 240"
    Write-Log "    python -m apt_engine.cli collect rents  --months 240"
    return
}
if ($validate -ne 0) {
    Write-Log "=== validate 위반이 남아 있습니다. 8~10단계를 건너뜁니다. ==="
    Write-Log "  무엇이 걸렸는지: $report"
    return
}

# ── 8~10단계 ─────────────────────────────────────────────────────────
# 여기까지 왔다는 것은 지역 × 월 × 거래유형 격자가 다 찼다는 뜻이다.
Write-Log "=== 품질 게이트 통과. 8~10단계 시작 ==="
Invoke-Step "8 투자점수" @("rank", "--save") | Out-Null
Invoke-Step "9 백테스트" @("backtest", "run") | Out-Null
Invoke-Step "10 로직 보정 (가중치 재학습)" @("backtest", "weights") | Out-Null
Write-Log "=== 후처리 끝 ==="
