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
#
# ── 시도별 병렬 ────────────────────────────────────────────────────────
# 서울·경기·인천을 각각 별도 프로세스로 돌린다. 병목이 네트워크 대기라 3배 가까이
# 빨라진다. 셋이 같은 SQLite 파일에 쓰지만 (거래월 × 시군구) 마다 짧게 커밋하고,
# busy_timeout 이 60초라 밀린 쪽이 기다렸다 쓴다.
#
# ⚠ 여기에 snapshot·match 같은 **긴 쓰기 트랜잭션**을 같이 돌리면 안 된다. 60초를
#   넘겨서 수집이 죽는다. 실제로 snapshot 을 같이 돌려 매매 수집을 통째로 날린 적이 있다.
#
# ⚠ 다음 단계로 넘어가기 전에 반드시 **끝날 때까지 기다려야** 한다.
#   Wait-Process 파이프라인이 즉시 반환해버려서 4개 단계가 한꺼번에 뜬 적이 있다
#   (프로세스 24개). Start-Process 가 돌려준 객체의 WaitForExit() 를 직접 부른다.
#
# ── 왜 두 번씩 도는가 ──────────────────────────────────────────────────
# 1차에서 네트워크가 끊겨 실패한 (거래월 × 시군구) 를 2차가 다시 받는다.
# 건너뛰기는 OK/EMPTY 만 하고 FAILED 는 다시 받으므로, 2차는 실패분만 짧게 돈다.

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "collect.log"
$sidos = @("서울", "경기", "인천")

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# 작업 스케줄러 콘솔은 cp949 다. 파이썬이 한글 안내문(em-dash 등)을 찍다가
# UnicodeEncodeError 로 죽은 적이 있다 — 한도 소진을 곱게 처리하고도 exit 1 이 됐다.
$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo
Write-Log "=== 수집 시작 (PID $PID) ==="

function Invoke-Phase($what, $label) {
    foreach ($pass in 1, 2) {
        Write-Log "$label ${pass}차 시작 - 시도 $($sidos.Count)개 병렬"
        $procs = @()
        foreach ($sido in $sidos) {
            $out = Join-Path $logDir "collect.$what.$sido.log"
            $p = Start-Process -FilePath $python `
                -ArgumentList @("-m", "apt_engine.cli", "collect", $what,
                                "--months", "240", "--sido", $sido) `
                -NoNewWindow -PassThru `
                -RedirectStandardOutput $out -RedirectStandardError "$out.err"
            $procs += [pscustomobject]@{ Sido = $sido; Proc = $p }
        }
        # WaitForExit() 를 직접 부른다. Wait-Process 파이프라인은 즉시 반환한 적이 있다.
        foreach ($e in $procs) {
            $e.Proc.WaitForExit()
            Write-Log "  $($e.Sido) 종료 (exit $($e.Proc.ExitCode))"
        }
        Write-Log "$label ${pass}차 종료"
    }
}

Invoke-Phase "trades" "매매"
Invoke-Phase "rents" "전월세"

Write-Log "=== 수집 끝. 다음: match -> snapshot -> validate ==="
