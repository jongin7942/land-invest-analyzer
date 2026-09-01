# 친구들에게 링크 보내기 (윈도우)
#
# 이 PC 에서 서버를 띄우고, Cloudflare 터널로 https 주소를 하나 받는다.
# 공유기 설정(포트포워딩)도 공인 IP 도 필요 없다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\share.ps1
#
# 먼저 초대할 사람을 만들어 두세요 (한 번만):
#   python -m apt_engine.cli invite add 철수
#   python -m apt_engine.cli invite add 영희
#
# 그러면 이 스크립트가 **사람마다 다른 링크**를 만들어 줍니다.
# 하나가 새어 나가면 그 한 명만 끊으면 됩니다:
#   python -m apt_engine.cli invite revoke 철수
#
# ⚠ 이 PC 가 켜져 있는 동안만 열립니다. 끄면 링크도 죽습니다.
# ⚠ 실행할 때마다 주소가 바뀝니다. 매번 새 링크를 보내야 합니다.

param(
  [int]$Port = 5001,
  [switch]$ShowRankings   # 잠금(가중치 미학습) 우회까지 보여줄 때만
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:APT_PUBLIC = "1"
$env:PORT = "$Port"
if ($ShowRankings) {
  $env:APT_ALLOW_UNLOCK = "1"
  Write-Host "※ 잠금 우회를 열었습니다. 순위 화면에 '투자 판단 근거가 아님' 경고가 그대로 나갑니다." -ForegroundColor Yellow
}

# 초대한 사람이 있는지 먼저 본다 — 없으면 주소를 아는 누구나 들어온다
$invites = & python -m apt_engine.cli invite list 2>&1 | Out-String
if ($invites -match "초대한 사람이 없습니다") {
  Write-Host ""
  Write-Host "!! 초대한 사람이 없습니다 — 지금 링크를 보내면 주소를 아는 누구나 들어옵니다." -ForegroundColor Red
  Write-Host "   먼저 이렇게 한 명씩 초대하세요:" -ForegroundColor Red
  Write-Host "     python -m apt_engine.cli invite add 철수" -ForegroundColor White
  Write-Host ""
  $go = Read-Host "그래도 진행할까요? (y/N)"
  if ($go -ne "y") { exit 1 }
}

# cloudflared 가 없으면 받는다 (설치가 아니라 실행 파일 하나)
$cf = Join-Path $root "cloudflared.exe"
if (-not (Test-Path $cf)) {
  Write-Host "cloudflared 내려받는 중..." -ForegroundColor Cyan
  Invoke-WebRequest -OutFile $cf `
    -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
}

Write-Host "서버 시작 (포트 $Port)..." -ForegroundColor Cyan
$server = Start-Process python -ArgumentList "serve.py" -PassThru -NoNewWindow
Start-Sleep -Seconds 3

# 터널 로그를 파일로 받아서 주소를 뽑는다 — 눈으로 찾아 옮겨 적지 않도록
$log = Join-Path $env:TEMP "apt-tunnel.log"
Remove-Item $log -ErrorAction SilentlyContinue
$tunnel = Start-Process $cf `
  -ArgumentList "tunnel","--url","http://127.0.0.1:$Port" `
  -PassThru -NoNewWindow -RedirectStandardError $log -RedirectStandardOutput "$log.out"

Write-Host "터널 여는 중..." -ForegroundColor Cyan
$url = $null
foreach ($i in 1..40) {
  Start-Sleep -Milliseconds 500
  if (Test-Path $log) {
    $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" `
         -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value; break }
  }
}

try {
  if (-not $url) {
    Write-Host "주소를 찾지 못했습니다. 로그를 보세요: $log" -ForegroundColor Red
  } else {
    $env:APT_SITE_URL = $url
    Write-Host ""
    Write-Host ("=" * 62) -ForegroundColor DarkGray
    Write-Host " 아래 링크를 각자에게 카톡으로 보내세요 (사람마다 다릅니다)" -ForegroundColor Green
    Write-Host ("=" * 62) -ForegroundColor DarkGray
    Write-Host ""
    & python -m apt_engine.cli invite links --url $url
    Write-Host ("=" * 62) -ForegroundColor DarkGray
    Write-Host " 이 창을 닫으면 링크도 닫힙니다." -ForegroundColor DarkGray
    Write-Host " 누가 들어왔는지:  python -m apt_engine.cli invite list" -ForegroundColor DarkGray
    Write-Host " 한 명 끊기:       python -m apt_engine.cli invite revoke 철수" -ForegroundColor DarkGray
    Write-Host ""
  }
  Wait-Process -Id $tunnel.Id
} finally {
  foreach ($proc in @($server, $tunnel)) {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
  }
  Write-Host "`n서버와 터널을 닫았습니다." -ForegroundColor DarkGray
}
