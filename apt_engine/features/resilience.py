"""하락기 실측 방어력 — "전부 떨어질 때 이 단지는 얼마나 덜 빠졌나".

── 왜 필요한가 (종인님 2026-09-04) ─────────────────────────────────
"부동산이 전부 떨어지는 시점이었으면 얼마나 비교적 더 방어가 됐냐로 봐야 되는데."
맞는 지적이었고, 그때까지 모델에는 **실측 방어력이 없었다.** downside_defense 는
전세가율에서 나온 이론값이지, 실제로 떨어질 때 어땠는지를 본 게 아니다.

가장 최근 전체 하락기는 2022년 고점 → 2023년 저점이다. 우리 데이터로 단지마다
그 낙폭을 재고, 같은 시군구 중앙 낙폭을 빼서 **상대 방어력**을 만든다.

    상대 방어 = (이 단지 낙폭) − (같은 시군구 중앙 낙폭)
    +5%p 면 동네보다 5%p 덜 빠졌다는 뜻이다.

고점·저점은 구간 최대/최소가 아니라 **구간 중앙값**으로 잡는다. 한 건 거래가
고점이나 저점을 만들면 낙폭이 잡음이 된다(그렇게 재면 낙폭 중앙값이 -25% 로
부풀었고, 중앙값으로 재면 -17.6% 였다).

── 재보니 나온 것 — 이 값은 알파가 아니라 방어다 ────────────────────
84㎡ 3,061개 · 59㎡ 2,068개, 시군구 대비 상대 방어 5분위별로 이후(2023 저점 →
2025) 회복률을 보면:

    가장 덜 빠진 단지(+10%p)   회복  +0.3%  (59㎡ -1.6%)
    가장 많이 빠진 단지(-7%p)   회복 +10.1%  (59㎡ +8.9%)
    순위상관 -0.30 (두 면적대 모두)

**덜 빠진 단지가 이후엔 덜 오른다.** 평균회귀다. 그러니 "덜 빠졌다" 를 상승
점수로 쓰면 저점 매수자에게 정확히 반대 신호를 준다.

그런데 고점 대비 현재 위치는 반대로 나온다.

    가장 덜 빠진 단지   2025 현재 고점 대비  -5.8%
    가장 많이 빠진 단지                     -17.1%

즉 사이클 전체로는 덜 빠진 쪽이 여전히 덜 손해다. 되튀긴 폭이 빠진 폭을 다
메우지 못했다. 두 사실이 동시에 참이다:

    보유 중 손실을 막는 힘(방어)     → 덜 빠진 단지가 낫다
    저점에서 사서 얻는 상승(알파)   → 많이 빠진 단지가 낫다

그래서 이 feature 는 **하방(RISK) 쪽에만** 쓴다. ranking/lists.py 의 _downside 가
전세가율 이론값 대신 이 실측값을 우선하고, 상승 점수에는 절대 더하지 않는다
(§45 role 분리 — 한 feature 는 한 role 만).

── 한계 ─────────────────────────────────────────────────────────────
· 하락기 한 번(2022→2023)의 관측이다. 다음 하락기에 같은 단지가 같은 순서로
  버틴다는 보장은 없다. 다만 그 하락기는 금리·규제가 겹친 전면 하락이라, 단지
  고유의 버티는 힘을 보기엔 좋은 표본이다.
· 2021~2023년에 거래가 없던 단지(신축·소형)는 값이 없다. 없으면 없다고 둔다.
"""
from __future__ import annotations

import sqlite3
import statistics
from functools import lru_cache

from apt_engine.features.base import Feature, Status
from apt_engine.trace import Calc, Evidence

KEY = "crash_resilience"

# 가장 최근 전면 하락기. 고점 구간과 저점 구간을 달 단위로 고정한다.
PEAK_FROM, PEAK_TO = "202107", "202212"
TROUGH_FROM, TROUGH_TO = "202301", "202312"

MIN_MONTHS = 4          # 구간 안에 이보다 적은 달만 있으면 안 잰다 (잡음)
MIN_PEERS = 8           # 시군구 중앙 낙폭을 낼 최소 단지 수

MEASURE_NOTE = ("2022년 고점→2023년 저점 한 번의 관측입니다. 다음 하락기에 같은 순서로 "
                "버틴다는 보장은 없습니다. 상승 점수에는 쓰지 않습니다(방어 전용)")


def _drawdown(conn: sqlite3.Connection, complex_id: int, band: str) -> tuple[float, int, int] | None:
    rows = conn.execute(
        "SELECT as_of_ym, representative_price p FROM price_snapshot "
        " WHERE complex_id = ? AND area_band = ? AND as_of_ym BETWEEN ? AND ?",
        (complex_id, band, PEAK_FROM, TROUGH_TO)).fetchall()
    peak = [r["p"] for r in rows if PEAK_FROM <= r["as_of_ym"] <= PEAK_TO]
    trough = [r["p"] for r in rows if TROUGH_FROM <= r["as_of_ym"] <= TROUGH_TO]
    if len(peak) < MIN_MONTHS or len(trough) < MIN_MONTHS:
        return None
    return statistics.median(trough) / statistics.median(peak) - 1.0, len(peak), len(trough)


_REGION_CACHE: dict[tuple[str, str, str], tuple[float | None, int]] = {}


def _db_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return str(row[2]) if row else ""


def _region_median(conn: sqlite3.Connection, lawd_cd: str, band: str) -> tuple[float | None, int]:
    """같은 시군구 단지들의 낙폭 중앙값. 같은 DB 파일 안에서는 한 번만 계산한다.

    캐시 열쇠에 DB 경로를 넣는다 — 테스트는 임시 DB 를 쓰므로, 경로 없이 캐시하면
    운영 DB 의 값이 테스트로 새어 들어간다.
    """
    key = (_db_path(conn), lawd_cd, band)
    if key in _REGION_CACHE:
        return _REGION_CACHE[key]
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM complex WHERE lawd_cd = ?", (lawd_cd,))]
    dds = []
    for cid in ids:
        d = _drawdown(conn, cid, band)
        if d is not None:
            dds.append(d[0])
    out = (None, len(dds)) if len(dds) < MIN_PEERS else (statistics.median(dds), len(dds))
    _REGION_CACHE[key] = out
    return out


def crash_resilience(conn: sqlite3.Connection, complex_id: int, band: str) -> Feature:
    row = conn.execute("SELECT lawd_cd FROM complex WHERE id = ?", (complex_id,)).fetchone()
    if not row or not row["lawd_cd"]:
        return Feature.missing(KEY, "시군구를 몰라 비교군을 만들 수 없습니다")
    mine = _drawdown(conn, complex_id, band)
    if mine is None:
        return Feature.missing(KEY, f"{PEAK_FROM[:4]}~{TROUGH_TO[:4]}년 거래가 적어 낙폭을 못 쟀습니다")
    region, n_peers = _region_median(conn, row["lawd_cd"], band)
    if region is None:
        return Feature.missing(KEY, f"같은 시군구에 비교할 단지가 {n_peers}개뿐입니다(최소 {MIN_PEERS})")

    dd, n_peak, n_trough = mine
    value = dd - region
    calc = Calc(
        value=value, unit="%p",
        formula="(이 단지 낙폭) − (같은 시군구 중앙 낙폭)",
        inputs={"고점 구간": f"{PEAK_FROM}~{PEAK_TO} (중앙값, {n_peak}개월)",
                "저점 구간": f"{TROUGH_FROM}~{TROUGH_TO} (중앙값, {n_trough}개월)"},
        intermediates={"이 단지 낙폭": f"{dd:+.1%}",
                       "시군구 중앙 낙폭": f"{region:+.1%} (단지 {n_peers}개)",
                       "상대 방어": f"{value:+.1%}p",
                       "뜻": ("+면 동네보다 덜 빠졌습니다. 덜 빠진 단지는 이후 덜 오르는 것으로 "
                             "측정됐으므로(순위상관 -0.30) 방어에만 쓰고 상승에는 쓰지 않습니다"),
                       "주의": MEASURE_NOTE},
        evidence=(Evidence(source="자체 측정 — price_snapshot 2021-07~2023-12"),),
        grade="ESTIMATED")
    confidence = 0.8 if min(n_peak, n_trough) >= 8 else 0.55
    return Feature(key=KEY, value=value, unit="", confidence=confidence, status=Status.OK,
                   calc=calc, detail={"낙폭": f"{dd:+.1%}", "시군구": f"{region:+.1%}",
                                      "상대": f"{value:+.1%}p"}).with_confidence(confidence)


def all_features(conn: sqlite3.Connection, complex_id: int, band: str) -> list[Feature]:
    return [crash_resilience(conn, complex_id, band)]
