# 아파트 투자 후보 — 공유 링크 발급 창 (바탕화면 "아파트 투자후보 공유링크" 가 이 파일을 실행)
# 약국 앱(눈뜬개국)의 share_link.ps1 과 같은 구조다.
# 1) 서버가 안 떠 있으면 띄우고 2) cloudflared 임시 터널을 열어 공개 주소를 받고
# 3) 받는 사람 이름을 물어 1회용 초대 링크를 만들어 클립보드에 복사한다.
# 이 창을 닫으면 터널이 닫혀 전원 접속이 끊긴다. 특정 사람만 끊기: http://127.0.0.1:5088/admin/share
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
trap { Write-Host ""; Write-Host "  오류: $($_.Exception.Message)" -ForegroundColor Red; Write-Host "  (서버가 재시작 중이었거나 인터넷이 끊겼을 수 있습니다. 창을 닫고 바로가기를 다시 실행하세요.)" -ForegroundColor Yellow; Read-Host "  Enter 를 누르면 닫힙니다"; exit 1 }
$proj = "C:\Users\jongi\land-invest-analyzer"
$py = Join-Path $proj ".venv\Scripts\python.exe"
$appPy = Join-Path $proj "web\app.py"
$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$port = 5088
$local = "http://127.0.0.1:$port"
$logDir = Join-Path $proj "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("tunnel_apt_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Up { try { (Invoke-WebRequest "$local/api/status" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false } }
function Invoke-Api($Uri, $Body) {
    for ($i = 1; $i -le 5; $i++) {
        try { return Invoke-RestMethod -Method Post -Uri $Uri -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($Body)) -TimeoutSec 20 }
        catch { if ($i -eq 5) { throw }; Write-Host "  서버 응답 대기 중($i/5)..." -ForegroundColor DarkGray; Start-Sleep 3 }
    }
}

Write-Host ""
Write-Host "  아파트 투자 후보 · 공유 링크" -ForegroundColor Cyan
Write-Host "  ──────────────────────────────" -ForegroundColor DarkGray

if (-not (Up)) {
    Write-Host "  서버 켜는 중..." -ForegroundColor Yellow
    Start-Process -FilePath $py -ArgumentList "`"$appPy`"" -WorkingDirectory $proj -WindowStyle Hidden
    $t = 0
    while (-not (Up) -and $t -lt 60) { Start-Sleep 1; $t++ }
    if (-not (Up)) { Write-Host "  서버가 안 뜹니다. 프로젝트 폴더에서 .venv\Scripts\python.exe web\app.py 를 직접 실행해 오류를 확인하세요." -ForegroundColor Red; Read-Host "Enter"; exit 1 }
}

# 이 앱의 터널만 정리한다 — 약국 앱(5077) 터널은 건드리지 않는다.
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*:$port*" } |
    ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
Start-Sleep 1
Get-ChildItem $logDir -Filter "tunnel_apt_*.log" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-3) } | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "  공개 주소 만드는 중(cloudflared)..." -ForegroundColor Yellow
$tunnel = Start-Process -FilePath $cf -ArgumentList "tunnel --url $local --no-autoupdate" -WindowStyle Hidden -RedirectStandardError $log -PassThru
$base = $null
$t = 0
while (-not $base -and $t -lt 60) {
    Start-Sleep 1; $t++
    if (Test-Path $log) {
        $m = [regex]::Match((Get-Content $log -Raw), "https://[a-z0-9-]+\.trycloudflare\.com")
        if ($m.Success) { $base = $m.Value }
    }
}
if (-not $base) { Write-Host "  공개 주소를 못 받았습니다(인터넷 연결 확인). 로그: $log" -ForegroundColor Red; Stop-Process -Id $tunnel.Id -Force; Read-Host "Enter"; exit 1 }
Write-Host "  공개 주소가 열리길 기다리는 중(보통 10~40초)..." -ForegroundColor Yellow
$t = 0; $ok = $false
while (-not $ok -and $t -lt 90) {
    try { $null = Invoke-WebRequest "$base/api/status" -UseBasicParsing -TimeoutSec 5; $ok = $true }
    catch { if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 403) { $ok = $true } }
    if (-not $ok) { Start-Sleep 2; $t += 2 }
}
if (-not $ok) { Write-Host "  주소가 아직 안 열립니다. 1분쯤 뒤 링크를 보내보세요." -ForegroundColor Yellow }
Invoke-Api "$local/api/admin/share/base" (@{url = $base} | ConvertTo-Json) | Out-Null
Write-Host "  공개 주소: $base" -ForegroundColor Green
Write-Host ""

function New-Link {
    $label = Read-Host "  링크 받을 사람 이름"
    if (-not $label) { $label = "이름없음" }
    $r = Invoke-Api "$local/api/admin/share/create" (@{label = $label} | ConvertTo-Json)
    Set-Clipboard -Value $r.link
    Write-Host ""
    Write-Host "  ▶ $label 님에게 보낼 링크 (클립보드에 복사됨, 카톡에 붙여넣기):" -ForegroundColor Cyan
    Write-Host "    $($r.link)" -ForegroundColor White
    Write-Host "    · 처음 연 기기 한 곳에서만 열립니다. 남에게 다시 보내도 안 열립니다." -ForegroundColor DarkGray
    Write-Host ""
}

New-Link
while ($true) {
    Write-Host "  [1] 링크 하나 더   [2] 접속자 보기·끊기(브라우저)   [3] 공유 전부 종료" -ForegroundColor Yellow
    $k = Read-Host "  선택"
    if ([string]::IsNullOrWhiteSpace($k)) { Start-Sleep 1; continue }
    switch ($k) {
        "1" { New-Link }
        "2" { Start-Process "$local/admin/share" }
        "3" {
            try { Invoke-RestMethod -Method Post -Uri "$local/api/admin/share/base" -ContentType "application/json" -Body '{"url":""}' | Out-Null } catch {}
            Stop-Process -Id $tunnel.Id -Force
            Write-Host "  터널을 닫았습니다. 전원 접속이 끊겼습니다." -ForegroundColor Green
            Start-Sleep 2
            exit 0
        }
    }
}
