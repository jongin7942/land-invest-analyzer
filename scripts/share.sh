#!/usr/bin/env bash
# scripts/share.ps1 의 맥·리눅스 판.
#
# 먼저 초대할 사람을 만들어 두세요 (한 번만):
#   python -m apt_engine.cli invite add 철수
set -euo pipefail
cd "$(dirname "$0")/.."

export APT_PUBLIC=1
export PORT="${PORT:-5001}"
[ "${SHOW_RANKINGS:-}" = "1" ] && export APT_ALLOW_UNLOCK=1

if python -m apt_engine.cli invite list 2>&1 | grep -q "초대한 사람이 없습니다"; then
  echo
  echo "!! 초대한 사람이 없습니다 — 지금 링크를 보내면 주소를 아는 누구나 들어옵니다."
  echo "   먼저:  python -m apt_engine.cli invite add 철수"
  echo
  read -r -p "그래도 진행할까요? (y/N) " go
  [ "$go" = "y" ] || exit 1
fi

command -v cloudflared >/dev/null || {
  echo "cloudflared 가 필요합니다: brew install cloudflared" >&2; exit 1; }

python serve.py >/dev/null 2>&1 &
server=$!
log="$(mktemp)"
cloudflared tunnel --url "http://127.0.0.1:$PORT" >"$log" 2>&1 &
tunnel=$!
trap 'kill $server $tunnel 2>/dev/null || true; rm -f "$log"' EXIT

url=""
for _ in $(seq 40); do
  sleep 0.5
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)
  [ -n "$url" ] && break
done

if [ -z "$url" ]; then
  echo "주소를 찾지 못했습니다:"; cat "$log"; exit 1
fi

echo
echo "=============================================================="
echo " 아래 링크를 각자에게 카톡으로 보내세요 (사람마다 다릅니다)"
echo "=============================================================="
echo
python -m apt_engine.cli invite links --url "$url"
echo "=============================================================="
echo " Ctrl+C 로 닫습니다."
echo " 누가 들어왔는지:  python -m apt_engine.cli invite list"
echo " 한 명 끊기:       python -m apt_engine.cli invite revoke 철수"
echo
wait $tunnel
