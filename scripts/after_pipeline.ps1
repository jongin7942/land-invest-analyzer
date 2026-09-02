# 파이프라인이 끝나면 DB 쓰기 작업을 이어서 한다.
#
# 백테스트가 몇십 분씩 DB 를 잡고 있어서 `rule import` 가 database is locked 로
# 죽는다. 사람이 지켜보다 넣는 대신, 파이프라인 프로세스가 사라지는 것을 보고
# 자동으로 잇는다. 파이프라인 전체(backtest-weights 포함)가 끝난 뒤에 시작해야
# 가중치 학습이 잠금에 걸리지 않는다.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$log = Join-Path $root "logs\after_pipeline.log"
$ALL = @("init","match","snapshot","validate","backtest-plan","backtest-run","backtest-weights")

function Say($m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Say "대기 시작 — 파이프라인 전 단계가 끝나기를 기다립니다"

# PID 를 기다리지 않는다. 감시견이 파이프라인을 죽이고 되살리면 그 PID 가
# 사라지면서, 아직 안 끝났는데 이어받아 DB 잠금이 다시 겹친다.
# '끝난 단계가 전부 적혔고 도는 프로세스도 없다' 일 때만 시작한다.
while ($true) {
    $done = if (Test-Path "$root\logs\pipeline.state") {
        @(Get-Content "$root\logs\pipeline.state" -Encoding UTF8 | Where-Object { $_ })
    } else { @() }
    $left = @($ALL | Where-Object { $done -notcontains $_ })
    $busy = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
              Where-Object { $_.CommandLine -like '*apt_engine.cli*' -and
                             $_.CommandLine -notlike '*apt_app*' })
    if ($left.Count -eq 0 -and $busy.Count -eq 0) { break }
    Start-Sleep -Seconds 60
}

Say "파이프라인 종료 확인 · 이어서 진행"

foreach ($step in @(
    @{ name = "init (마이그레이션 022 적용)"; args = @("init") },
    @{ name = "취득세 과거 규칙 임포트";      args = @("rule", "import", "tax", "rules/tax_history.csv") }
)) {
    Say "─── $($step.name) 시작 ───"
    $out = & python -u -m apt_engine.cli @($step.args) 2>&1
    $out | Add-Content -Path $log -Encoding UTF8
    Say "─── $($step.name) 종료 (exit $LASTEXITCODE) ───"
}
Say "완료"
