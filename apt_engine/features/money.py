"""돈의 흐름 — 두 종류를 절대 합치지 않는다 (지시서 §8·§9·§10).

**왜 하나로 뭉치면 안 되는가**

지역에 들어오는 돈은 원인이 다른 두 갈래다.

  A. 지역 소득 유입 (Income Flow)
     일자리·사업체·성과급이 늘어 **그 지역 사람의 구매력 자체가** 커진 것.
     원인이 지역 안에 있어서 비교적 오래 간다.

  B. 가격 사다리 이동 (Capital Migration Flow)
     서울/대장 가격이 올라 원래 거기 사려던 사람이 더 싼 곳으로 **밀려
     내려온** 것. 원인이 지역 밖에 있어서, 위 칸이 멈추면 같이 멈춘다.

둘을 더해 '자금 유입 70점' 으로 만들면 **왜 오를 것인지 설명할 수 없다.**
같은 70점이라도 A 는 지역이 좋아진 것이고 B 는 서울이 비싸진 것이다.
대응도 다르다 — A 는 기다려도 되고 B 는 위 칸이 식으면 끝난다.

**§49 금지사항이 여기서 특히 잘 깨진다**

  §49-2 특정 지역 이름 자체에 점수 금지
  §49-3 삼성·GTX 같은 호재 자체에 점수 금지

"삼성전자가 있으니 평택은 소득이 는다" 는 추론은 데이터가 아니다.
그래서 Income Flow 는 `region_income` 표에 **사람이 출처와 함께 넣은
값이 있을 때만** 계산되고, 없으면 0 이 아니라 '확인 불가' 로 남는다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from apt_engine.features.base import Feature, Status
from apt_engine.trace import Calc

# 소득 지표별 가중치. 고용자 수가 늘었다는 것이 성과급 한 번보다 오래 간다.
INCOME_WEIGHT = {
    "고용자수": 1.00,
    "고소득일자리": 0.90,
    "사업체수": 0.70,
    "평균소득": 0.70,
    "산업단지가동": 0.50,
    "성과급": 0.30,      # 한 해짜리다. 구매력의 지속적 상승이 아니다.
}

# 이 아래로 오래된 자료는 쓰지 않는다. 소득·고용은 분기~연 단위로 나온다.
INCOME_STALE_MONTHS = 24

# 사다리 이동을 흐름으로 인정하는 최소 조건 (§8-B 주석 참고)
MIN_UPPER_RISE = 0.05     # 위 칸이 최소 5% 는 올라야 밀려 내려올 것이 생긴다
MIN_GAP = 0.10            # 가격차가 10% 는 남아야 내려올 이유가 있다
MIN_OVERLAP = 0.30        # 구매자가 겹치지 않으면 남의 동네 이야기다


def _months_between(a: str, b: str) -> int:
    """YYYYMM 두 개의 개월 차."""
    ya, ma = int(a[:4]), int(a[4:6])
    yb, mb = int(b[:4]), int(b[4:6])
    return abs((yb - ya) * 12 + (mb - ma))


# ── §8-A 지역 소득 유입 ───────────────────────────────────────────────

@dataclass(frozen=True)
class Income:
    value: float | None
    used: dict[str, float]
    skipped: dict[str, str]
    reason: str | None = None


def income_flow(conn: sqlite3.Connection, *, lawd_cd: str, as_of_ym: str) -> Income:
    """지역 소득 유입.

    **없으면 0 이 아니라 None 이다.** 0 으로 두면 "소득이 안 늘었다" 는
    관측이 되어 버리는데, 실제로는 우리가 안 본 것이다. 그 둘은 다르다.
    """
    rows = conn.execute(
        "SELECT metric, yoy_change_pct, as_of_ym, confidence, last_verified "
        "FROM region_income WHERE lawd_cd = ? AND as_of_ym <= ? "
        "AND yoy_change_pct IS NOT NULL",
        (lawd_cd, as_of_ym)).fetchall()
    if not rows:
        return Income(None, {}, {},
                      reason="이 지역의 소득·고용 자료가 없습니다")

    used: dict[str, float] = {}
    skipped: dict[str, str] = {}
    # 지표별로 가장 최근 것 하나만 쓴다 (같은 지표가 여러 시점에 있다)
    latest: dict[str, sqlite3.Row] = {}
    for r in rows:
        m = r["metric"]
        if m not in latest or r["as_of_ym"] > latest[m]["as_of_ym"]:
            latest[m] = r

    for metric, r in latest.items():
        if not r["last_verified"]:
            skipped[metric] = "확인되지 않은 입력이라 계산에서 뺐습니다"
            continue
        age = _months_between(r["as_of_ym"], as_of_ym)
        if age > INCOME_STALE_MONTHS:
            skipped[metric] = f"{age}개월 지난 자료라 뺐습니다"
            continue
        w = INCOME_WEIGHT.get(metric)
        if w is None:
            skipped[metric] = "가중치가 정의되지 않은 지표입니다"
            continue
        used[metric] = r["yoy_change_pct"] * w

    if not used:
        return Income(None, {}, skipped,
                      reason="쓸 수 있는 소득 지표가 하나도 없습니다")

    # 가중 평균. 지표가 하나뿐이면 그 하나로 판단한다는 뜻이라
    # 신뢰도는 아래 feature 에서 낮춘다.
    total_w = sum(INCOME_WEIGHT[m] for m in used)
    value = sum(used.values()) / total_w
    return Income(value, used, skipped)


def income_feature(inc: Income) -> Feature:
    if inc.value is None:
        return Feature.missing("income_flow", inc.reason or "계산하지 못했습니다")
    # 지표 하나로는 지역 구매력을 말할 수 없다. 3개는 있어야 믿는다.
    conf = min(1.0, 0.30 + 0.20 * len(inc.used))
    return Feature(
        key="income_flow", value=inc.value, unit="", confidence=conf,
        status=Status.OK,
        detail={"쓴 지표": {k: f"{v:+.1%}" for k, v in inc.used.items()},
                "뺀 지표": inc.skipped,
                "뜻": "이 지역 사람의 구매력 자체가 늘고 있는가"},
        calc=Calc("income_flow", "지표별 전년대비 변화율의 가중평균",
                  {"지표수": len(inc.used)}))


# ── §8-B 가격 사다리 이동 ─────────────────────────────────────────────

@dataclass(frozen=True)
class Migration:
    value: float | None
    from_region: str | None
    upper_rise: float | None
    gap: float | None
    overlap: float | None
    reason: str | None = None


def migration_flow(conn: sqlite3.Connection, *, lawd_cd: str,
                   as_of_ym: str) -> Migration:
    """가격 사다리 이동.

    위 칸이 올랐다는 사실만으로는 아무것도 아니다(§49-2). 세 가지가
    **동시에** 성립해야 돈이 내려올 이유가 생긴다.

        위 칸이 올랐다  ×  가격차가 아직 남았다  ×  구매자가 겹친다

    하나라도 0 이면 곱해서 0 이다 — 합이 아니라 곱인 이유가 이것이다.
    """
    rows = conn.execute(
        "SELECT from_lawd_cd, upper_rise_pct, gap_pct, buyer_overlap, as_of_ym "
        "FROM migration_flow WHERE to_lawd_cd = ? AND as_of_ym <= ? "
        "AND upper_rise_pct IS NOT NULL ORDER BY as_of_ym DESC",
        (lawd_cd, as_of_ym)).fetchall()
    if not rows:
        return Migration(None, None, None, None, None,
                         reason="이 지역으로 내려오는 사다리 관측이 없습니다")

    best = None
    for r in rows:
        rise, gap, ov = r["upper_rise_pct"], r["gap_pct"], r["buyer_overlap"]
        if gap is None or ov is None:
            continue
        if rise < MIN_UPPER_RISE or gap < MIN_GAP or ov < MIN_OVERLAP:
            continue
        v = rise * gap * ov
        if best is None or v > best[0]:
            best = (v, r["from_lawd_cd"], rise, gap, ov)

    if best is None:
        return Migration(None, None, None, None, None,
                         reason="위 칸 상승·가격차·구매자 겹침 중 하나가 기준에 못 미칩니다")
    v, frm, rise, gap, ov = best
    return Migration(v, frm, rise, gap, ov)


def migration_feature(m: Migration) -> Feature:
    if m.value is None:
        return Feature.missing("migration_flow", m.reason or "계산하지 못했습니다")
    return Feature(
        key="migration_flow", value=m.value, unit="", confidence=min(1.0, m.overlap + 0.2),
        status=Status.OK,
        detail={"어디서": m.from_region,
                "위 칸 상승": f"{m.upper_rise:+.1%}",
                "남은 가격차": f"{m.gap:.1%}",
                "구매자 겹침": f"{m.overlap:.0%}",
                "뜻": "위 동네가 비싸져 이쪽으로 밀려 내려오는 돈이 있는가",
                "주의": "위 칸이 멈추면 이 흐름도 멈춥니다"},
        calc=Calc("migration_flow", "위칸상승 × 남은가격차 × 구매자겹침",
                  {"곱": "하나라도 0 이면 0"}))


# ── §9 Dual Flow Exposure ─────────────────────────────────────────────

# 두 흐름을 동시에 받는다고 인정하려면 **실제 데이터가 움직여야** 한다.
# 지역 이름으로는 절대 인정하지 않는다(§9 · §49-2).
DUAL_EVIDENCE = ("거래량 증가", "P25 상승", "중위가격 상승", "전세 유지")
MIN_DUAL_EVIDENCE = 2


@dataclass(frozen=True)
class DualFlow:
    exposed: bool
    evidence: list[str]
    reason: str


def dual_flow(income: Income, migration: Migration, *,
              volume_up: bool | None, p25_up: bool | None,
              median_up: bool | None, jeonse_held: bool | None) -> DualFlow:
    """두 종류의 돈을 동시에 받는 곳인가.

    ⚠ 두 흐름이 다 있다는 것만으로는 부족하다. **가격·거래 데이터가
      실제로 움직여야** 한다. 안 움직이면 그냥 "이론상 그럴 수 있는 곳" 이고,
      그건 투자 근거가 아니다.
    """
    if income.value is None or migration.value is None:
        missing = []
        if income.value is None:
            missing.append("지역 소득 유입")
        if migration.value is None:
            missing.append("사다리 이동")
        return DualFlow(False, [], f"{' · '.join(missing)}을 확인하지 못했습니다")

    found = [name for name, ok in zip(DUAL_EVIDENCE,
                                      (volume_up, p25_up, median_up, jeonse_held))
             if ok]
    if len(found) < MIN_DUAL_EVIDENCE:
        return DualFlow(
            False, found,
            f"두 흐름이 다 있지만 실제 데이터가 아직 안 움직입니다 "
            f"(확인 {len(found)}/{MIN_DUAL_EVIDENCE}개)")
    return DualFlow(True, found, "두 흐름을 다 받고 데이터도 움직입니다")


def dual_feature(d: DualFlow) -> Feature:
    if not d.exposed:
        # 0 이 아니라 '아니다' 다. 값을 준다 — 이건 관측된 판정이다.
        return Feature(key="dual_flow", value=0.0, unit="", confidence=0.6,
                       status=Status.OK,
                       detail={"판정": "아니오", "사유": d.reason,
                               "확인된 근거": d.evidence})
    return Feature(
        key="dual_flow", value=1.0, unit="",
        confidence=min(1.0, 0.4 + 0.15 * len(d.evidence)), status=Status.OK,
        detail={"판정": "예", "사유": d.reason, "확인된 근거": d.evidence,
                "뜻": "지역 소득과 밀려 내려온 돈을 동시에 받는 곳"})


# ── §10 Accessible Money Flow ─────────────────────────────────────────

def accessible_money_flow(*, income: Feature, migration: Feature,
                          leader_pressure: float | None,
                          buyer_overlap: float | None,
                          transmission_prob: float | None) -> Feature:
    """실제로 이 단지 구매자에게 닿는 돈.

    지역에 돈이 많아졌다고 모든 아파트가 오르지 않는다. 삼성 성과급이
    늘었다는 사실 자체에는 점수를 주지 않는다(§10) — 그 돈이 **이 가격대
    아파트를 사는 사람에게** 흘러야 한다.

        직접 소득 유입
      + 가격대 이동
      + 대장 상승압력 × 구매자 겹침 × 가격전파 확률
                        └── 이 세 개는 곱이다. 대장이 아무리 올라도
                            구매자가 안 겹치면 남의 동네 이야기다.
    """
    parts: dict[str, float] = {}
    missing: list[str] = []

    if income.usable:
        parts["지역 소득"] = income.value
    else:
        missing.append("지역 소득 유입")

    if migration.usable:
        parts["가격대 이동"] = migration.value
    else:
        missing.append("사다리 이동")

    if None not in (leader_pressure, buyer_overlap, transmission_prob):
        parts["대장 전파"] = leader_pressure * buyer_overlap * transmission_prob
    else:
        missing.append("대장 상승 전파")

    if not parts:
        return Feature.missing(
            "accessible_money_flow",
            "돈이 실제로 닿는지 확인할 자료가 하나도 없습니다: "
            + " · ".join(missing))

    value = sum(parts.values())
    # 세 갈래 중 몇 개를 실제로 봤는지가 곧 신뢰도다.
    conf = 0.25 + 0.25 * len(parts)
    return Feature(
        key="accessible_money_flow", value=value, unit="",
        confidence=min(1.0, conf), status=Status.OK,
        detail={"구성": {k: round(v, 4) for k, v in parts.items()},
                "못 본 것": missing,
                "뜻": "이 가격대 아파트를 살 사람에게 실제로 닿는 돈"},
        calc=Calc("accessible_money_flow",
                  "지역소득 + 가격대이동 + (대장압력 × 구매자겹침 × 전파확률)",
                  {"본 갈래": len(parts), "못 본 갈래": len(missing)}))


def all_features(conn: sqlite3.Connection, *, lawd_cd: str, as_of_ym: str,
                 leader_pressure: float | None = None,
                 buyer_overlap: float | None = None,
                 transmission_prob: float | None = None,
                 volume_up: bool | None = None, p25_up: bool | None = None,
                 median_up: bool | None = None,
                 jeonse_held: bool | None = None) -> list[Feature]:
    inc = income_flow(conn, lawd_cd=lawd_cd, as_of_ym=as_of_ym)
    mig = migration_flow(conn, lawd_cd=lawd_cd, as_of_ym=as_of_ym)
    f_inc, f_mig = income_feature(inc), migration_feature(mig)
    d = dual_flow(inc, mig, volume_up=volume_up, p25_up=p25_up,
                  median_up=median_up, jeonse_held=jeonse_held)
    return [f_inc, f_mig, dual_feature(d),
            accessible_money_flow(income=f_inc, migration=f_mig,
                                  leader_pressure=leader_pressure,
                                  buyer_overlap=buyer_overlap,
                                  transmission_prob=transmission_prob)]


__all__ = ["income_flow", "income_feature", "migration_flow", "migration_feature",
           "dual_flow", "dual_feature", "accessible_money_flow", "all_features",
           "Income", "Migration", "DualFlow", "INCOME_WEIGHT"]
