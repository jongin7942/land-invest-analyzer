"""1단계 자동 스크리닝 — 전국 아파트에 똑같이 적용되는 값만 쓴다.

여기서 쓰는 입력은 세 가지뿐이고 전부 이미 수집된 값이다.

    연식        사용승인연도. K-apt 기본정보에 있다
    현재 용적률  용적률이 낮을수록 지을 여지가 남는다
    평균 대지지분 대지면적 / 아파트 세대수. 대지지분이 사업성의 뿌리다

**이 단계에서 나오는 건 "후보인가"뿐이다.** 추가분담금·비례율 같은 숫자는
여기서 만들지 않는다. 그런 숫자는 정비계획·조합 자료를 사람이 넣은 단지에서만
나온다(2단계). 그래서 `score` 는 돈 단위가 아니라 0~1 의 순위용 점수이고,
이름도 '사업성'이 아니라 '스크리닝 점수'다.

값이 없는 항목은 0점으로 세지 않는다 — 없는 항목은 가중치 분모에서 빠지고,
`reason` 에 "대지면적 미입력"이 남는다. 0으로 세면 데이터가 없는 단지가
"사업성 나쁨"으로 둔갑한다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import ENGINE_VERSION, rules, units

# 재건축 연한(도시정비법). 이 값은 법이므로 상수로 둔다 — 다만 "30년 넘으면
# 재건축된다"는 뜻이 아니라 "그 전에는 안전진단 신청조차 못 한다"는 뜻이다.
MIN_AGE_YEARS = 30

# 1차 통과선. 현재 용적률이 이보다 높으면 추가로 지을 여지가 거의 없다.
MAX_CURRENT_FAR = 200.0

# 세대수 하한 — 너무 작으면 사업비 분담 구조가 성립하기 어렵다.
MIN_HOUSEHOLDS = 100

WEIGHTS = {"대지지분": 0.45, "용적률여유": 0.35, "연식": 0.20}

# 점수 정규화 구간. 이 값들은 '순위를 매기기 위한 눈금'이지 예측이 아니다.
LAND_SHARE_FLOOR_M2 = 20.0      # 세대당 대지지분 20㎡(약 6평) 이하는 0점
LAND_SHARE_CAP_M2 = 60.0        # 60㎡(약 18평) 이상은 만점
FAR_FULL_MARK = 80.0            # 현재 용적률 80% 이하는 만점 (저층 단지)
AGE_CAP_YEARS = 50


@dataclass(frozen=True)
class Candidate:
    complex_id: int
    name: str
    lawd_cd: str
    age_years: int | None
    current_far: float | None
    land_share_m2: float | None
    apt_households: int | None
    score: float
    parts: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    manual_status: str = "미조사"

    @property
    def land_share_pyeong(self) -> float | None:
        return None if self.land_share_m2 is None else units.to_pyeong(self.land_share_m2)

    @property
    def reason(self) -> dict:
        out = {
            "연식": f"{self.age_years}년" if self.age_years is not None else "확인 불가",
            "현재 용적률": f"{self.current_far:g}%" if self.current_far else "확인 불가",
            "평균 대지지분": (f"{self.land_share_m2:.1f}㎡ "
                        f"({self.land_share_pyeong:.1f}평)"
                        if self.land_share_m2 else "확인 불가 — 대지면적 미입력"),
            "항목별 점수": {k: round(v, 3) for k, v in self.parts.items()},
        }
        if self.missing:
            out["빠진 항목"] = self.missing
            out["주의"] = ("빠진 항목은 0점이 아니라 가중치에서 제외했습니다. "
                         "점수가 낮은 게 아니라 자료가 없는 것입니다")
        out["한계"] = ("연식·용적률·대지지분만 본 1차 선별입니다. "
                     "정비계획·조합 자료 없이는 사업성 금액을 계산하지 않습니다")
        return out


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def score_of(*, age_years: int | None, current_far: float | None,
             land_share_m2: float | None) -> tuple[float, dict, list[str]]:
    parts: dict[str, float] = {}
    missing: list[str] = []

    if land_share_m2 is None:
        missing.append("평균 대지지분(대지면적 미입력)")
    else:
        parts["대지지분"] = _clamp01(
            (land_share_m2 - LAND_SHARE_FLOOR_M2) / (LAND_SHARE_CAP_M2 - LAND_SHARE_FLOOR_M2))

    if current_far is None:
        missing.append("현재 용적률")
    else:
        # 통과선(200%)에서 0점, 저층 단지(80% 이하)에서 만점.
        parts["용적률여유"] = _clamp01(
            (MAX_CURRENT_FAR - current_far) / (MAX_CURRENT_FAR - FAR_FULL_MARK))

    if age_years is None:
        missing.append("사용승인연도")
    else:
        parts["연식"] = _clamp01((age_years - MIN_AGE_YEARS) / (AGE_CAP_YEARS - MIN_AGE_YEARS))

    denom = sum(WEIGHTS[k] for k in parts)
    if denom <= 0:
        return 0.0, parts, missing
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / denom
    return round(score, 4), parts, missing


def screen(conn: sqlite3.Connection, *, as_of: str | date, lawd_cd: str | None = None,
           min_age: int = MIN_AGE_YEARS, max_far: float = MAX_CURRENT_FAR,
           min_households: int = MIN_HOUSEHOLDS,
           require_far: bool = False) -> list[Candidate]:
    """1차 통과 단지를 점수순으로. 통과 조건은 전부 사실 기반이다."""
    day = rules.as_ymd(as_of)
    year = int(day[:4])

    sql = ("SELECT c.id, c.name, c.lawd_cd, c.approval_year, c.current_far, "
           "       c.land_area_m2, c.apt_households, "
           "       (SELECT manual_status FROM redev_candidate rc WHERE rc.complex_id = c.id) "
           "         AS manual_status "
           "  FROM complex c WHERE 1=1")
    params: list = []
    if lawd_cd:
        sql += " AND c.lawd_cd = ?"
        params.append(lawd_cd)

    out: list[Candidate] = []
    for r in conn.execute(sql, params):
        age = None if r["approval_year"] is None else year - int(r["approval_year"])
        if age is None or age < min_age:
            continue
        far = r["current_far"]
        if far is not None and float(far) > max_far:
            continue
        if far is None and require_far:
            continue
        households = r["apt_households"]
        if households is not None and int(households) < min_households:
            continue

        # 평균 대지지분 = 대지면적 / 아파트 세대수.
        # 오피스텔 세대수를 섞지 않는다(요구사항 26-2) — apt_households 만 쓴다.
        land_share = None
        if r["land_area_m2"] and households:
            land_share = float(r["land_area_m2"]) / int(households)

        score, parts, missing = score_of(
            age_years=age, current_far=None if far is None else float(far),
            land_share_m2=land_share)
        out.append(Candidate(
            complex_id=r["id"], name=r["name"], lawd_cd=r["lawd_cd"], age_years=age,
            current_far=None if far is None else float(far), land_share_m2=land_share,
            apt_households=households, score=score, parts=parts, missing=missing,
            manual_status=r["manual_status"] or "미조사"))

    out.sort(key=lambda c: (-c.score, c.name))
    return out


def save(conn: sqlite3.Connection, candidates: list[Candidate], *,
         as_of: str | date) -> int:
    """스크리닝 결과 저장. manual_status 는 사람이 정한 값이라 덮어쓰지 않는다."""
    day = rules.as_ymd(as_of)
    rank_by_region: dict[str, int] = {}
    saved = 0
    for c in candidates:
        rank_by_region[c.lawd_cd] = rank_by_region.get(c.lawd_cd, 0) + 1
        conn.execute(
            "INSERT INTO redev_candidate (complex_id, screened_at, as_of, age_years, "
            " current_far, land_share_m2, score, rank_in_region, reason_json, "
            " engine_version) VALUES (?, datetime('now','localtime'), ?,?,?,?,?,?,?,?) "
            "ON CONFLICT(complex_id) DO UPDATE SET "
            " screened_at=excluded.screened_at, as_of=excluded.as_of, "
            " age_years=excluded.age_years, current_far=excluded.current_far, "
            " land_share_m2=excluded.land_share_m2, score=excluded.score, "
            " rank_in_region=excluded.rank_in_region, reason_json=excluded.reason_json, "
            " engine_version=excluded.engine_version",
            (c.complex_id, day, c.age_years, c.current_far, c.land_share_m2, c.score,
             rank_by_region[c.lawd_cd],
             json.dumps(c.reason, ensure_ascii=False), ENGINE_VERSION))
        saved += 1
    return saved
