"""카카오톡 '나에게 보내기' CLI.  사용: kakao_send.py "본문" [링크URL]

stock-alert 의 config.json(kakao.rest_api_key / refresh_token)을 재사용하고,
카카오가 refresh_token 을 새로 주면 같은 파일에 되돌려 저장한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notify.kakao import refresh_access_token, send_memo  # noqa: E402

CFG = Path(r"C:\Users\jongi\stock-alert\config.json")
DEFAULT_LINK = "https://claude.ai/code/artifact/ee73a3e4-7be8-46d6-9461-4b2d2201eb6a"


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    link = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LINK
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    access, new_refresh = refresh_access_token(cfg["kakao"]["rest_api_key"], cfg["kakao"]["refresh_token"])
    if new_refresh:
        cfg["kakao"]["refresh_token"] = new_refresh
        CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    out = []
    for i in range(0, max(1, len(text)), 1900):
        out.append(send_memo(access, text[i:i + 1900], link))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if all(o.get("result_code") == 0 for o in out) else 1


if __name__ == "__main__":
    sys.exit(main())
