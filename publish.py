"""정적 사이트 재생성 + GitHub Pages 재배포.

pipeline.py --run 으로 데이터를 새로 갱신한 뒤 이걸 실행하면
GitHub Pages 공개 사이트가 최신 데이터로 다시 배포된다.

사용:
  python publish.py
"""
from __future__ import annotations

import os
import subprocess
import sys

from build_static import build

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "jongin7942",
    "GIT_AUTHOR_EMAIL": "jongin7942@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "jongin7942",
    "GIT_COMMITTER_EMAIL": "jongin7942@users.noreply.github.com",
}


def run(cmd):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, env=GIT_ENV)


def main():
    print("1) 정적 사이트 재생성...")
    build()

    print("\n2) git 커밋 + 푸시...")
    run(["git", "add", "docs"])
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], env=GIT_ENV)
    if result.returncode == 0:
        print("변경사항 없음. 재배포 불필요.")
        return
    run(["git", "commit", "-m", "데이터 갱신"])
    run(["git", "push"])
    print("\n완료. 몇 분 내로 GitHub Pages에 반영됩니다.")


if __name__ == "__main__":
    sys.exit(main())
