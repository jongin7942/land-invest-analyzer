# 지금 당장 카톡으로 공유할 수 있는 링크 만들기 (윈도우)
#
# 이 PC 에서 서버를 띄우고, Cloudflare 터널로 https 주소를 하나 받는다.
# 공유기 설정(포트포워딩)도 공인 IP 도 필요 없다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\share.ps1
#
# ⚠ 이 PC 가 켜져 있는 동안만 열린다. 끄면 링크도 죽는다.
# ⚠ 주소를 받은 사람은 누구나 들어온다. 코드를 걸려면 -Code 를 준다.

param(
  [string]$Code = "",
  [int]$Port = 5001
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 공개 모드로 켠다 — 잠금 우회(?unlock=1)를 막고 검색엔진 색인을 끈다
$env:APT_PUBLIC = "1"
$env:PORT = "$Port"
if ($Code -ne "") {
  $env:APT_ACCESS_CODE = $Code
  Write-Host "접속 코드: $Code  (링크 뒤에 ?code=$Code 를 붙여 보내면 한 번만 입력됩니다)" -ForegroundColor Yellow
}

# cloudflared 가 없으면 받는다 (설치가 아니라 실행 파일 하나)
$cf = Join-Path $root "cloudflared.exe"
if (-not (Test-Path $cf)) {
  Write-Host "cloudflared 내려받는 중..." -ForegroundColor Cyan
  $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
  Invoke-WebRequest -Uri $url -OutFile $cf
}

Write-Host "서버 시작 (포트 $Port)..." -ForegroundColor Cyan
$server = Start-Process -FilePath "python" -ArgumentList "serve.py" `
  -PassThru -NoNewWindow

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "터널 여는 중 — 아래 trycloudflare.com 주소를 카톡에 붙여넣으세요." -ForegroundColor Green
Write-Host "창을 닫으면 링크도 닫힙니다." -ForegroundColor DarkGray
Write-Host ""

try {
  & $cf tunnel --url "http://127.0.0.1:$Port"
} finally {
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
  Write-Host "`n서버와 터널을 닫았습니다." -ForegroundColor DarkGray
}
