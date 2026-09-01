#!/usr/bin/env bash
# scripts/share.ps1 의 맥·리눅스 판.
#   ./scripts/share.sh [접속코드]
set -euo pipefail
cd "$(dirname "$0")/.."

export APT_PUBLIC=1
export PORT="${PORT:-5001}"
[ $# -ge 1 ] && export APT_ACCESS_CODE="$1" && \
  echo "접속 코드: $1  (링크 뒤에 ?code=$1 를 붙여 보내면 한 번만 입력됩니다)"

command -v cloudflared >/dev/null || {
  echo "cloudflared 가 필요합니다: brew install cloudflared" >&2; exit 1; }

python serve.py &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT
sleep 3

echo
echo "아래 trycloudflare.com 주소를 카톡에 붙여넣으세요. Ctrl+C 로 닫습니다."
echo
cloudflared tunnel --url "http://127.0.0.1:$PORT"
