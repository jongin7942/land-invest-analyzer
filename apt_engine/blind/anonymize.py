"""익명 ID — 이름을 지워도 같은 순위가 나오는가 (지시서 §1).

지시서의 절대 원칙:

> 사용자가 검색한 지역 / 자주 언급한 단지 / watchlist / 이전 상담 내용 /
> 사용자의 관심도 — 이 변수들은 scoring 과 candidate generation 에
> **절대로** 사용하지 않는다.

문제는 이게 "안 쓰겠다" 는 다짐으로는 안 지켜진다는 점이다. 단지명 문자열이
feature 로 새어 들어가는 경로는 생각보다 많다 — 이름으로 정렬, 이름 해시,
이름에 든 브랜드, 심지어 dict 삽입 순서까지.

그래서 **Placebo Test** 를 만든다. 단지명·주소를 익명 ID 로 바꾼 상태에서
랭킹을 돌려서 결과가 같으면 이름이 점수에 영향을 주지 않은 것이다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


ANON_PREFIX = "CX"


def anon_id(complex_id: int, salt: str = "") -> str:
    """단지 하나의 익명 ID. 같은 salt 면 항상 같은 값이다(재현성)."""
    digest = hashlib.sha256(f"{salt}:{complex_id}".encode()).hexdigest()
    return f"{ANON_PREFIX}-{digest[:10]}"


@dataclass(frozen=True)
class Anonymizer:
    """단지 식별 정보를 가린다. 되돌리는 건 랭킹이 끝난 뒤에만."""
    salt: str = ""

    # 랭킹 계층이 절대 봐서는 안 되는 필드.
    HIDDEN = ("name", "name_norm", "apt_name", "road_addr", "jibun",
              "emd_name", "kapt_code", "builder")

    def hide(self, row: dict) -> dict:
        """식별 필드를 익명 ID 로 갈아끼운 사본."""
        cid = row.get("complex_id", row.get("id"))
        out = {k: v for k, v in row.items() if k not in self.HIDDEN}
        out["anon_id"] = anon_id(int(cid), self.salt)
        return out

    def hide_all(self, rows) -> list[dict]:
        return [self.hide(dict(r)) for r in rows]


def ranking_fingerprint(entries) -> list[tuple]:
    """랭킹 결과의 지문 — 이름을 뺀 (순위, 단지id, 점수).

    Placebo Test 는 이 지문이 익명화 전후로 같은지를 본다. 점수는 부동소수라
    아주 작은 차이는 허용하되, 순위가 하나라도 바뀌면 실패로 본다.
    """
    return [(i, int(e["complex_id"]), round(float(e["score"]), 9))
            for i, e in enumerate(entries, start=1)]
