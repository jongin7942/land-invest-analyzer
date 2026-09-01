"""프로덕션 서버.

`python apt_app.py` 의 Flask 개발 서버는 한 번에 한 요청만 제대로 처리한다.
링크를 공유하면 여러 명이 동시에 들어오고, 그때 개발 서버는 줄을 세운다 —
한 사람이 순위를 계산하는 동안 나머지는 빈 화면을 본다.

waitress 를 쓰는 이유는 **윈도우에서 돌기 때문**이다. gunicorn 은 윈도우를
지원하지 않고, 이 앱은 종인님 PC(윈도우)에서 터널로 공유하는 경로가 있다.
컴파일이 필요 없는 순수 파이썬이라 설치도 걸리지 않는다.

    python serve.py                 # 0.0.0.0:5001
    PORT=8080 python serve.py       # 포트 지정
"""
import os

from waitress import serve

from apt_app import PORT, app

if __name__ == "__main__":
    host = os.getenv("HOST") or "0.0.0.0"
    # 스레드 수: SQLite 는 읽기 전용 커넥션이라 병렬로 읽어도 안전하다.
    # 너무 크게 잡으면 랭킹 계산이 동시에 여러 개 돌아 PC 가 느려진다.
    threads = int(os.getenv("THREADS") or 8)
    print(f"→ http://{host}:{PORT}  (threads={threads})")
    serve(app, host=host, port=PORT, threads=threads,
          ident="apt", clear_untrusted_proxy_headers=True)
