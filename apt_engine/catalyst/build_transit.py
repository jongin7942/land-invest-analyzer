"""미개통 역 → 채점에 들어가는 호재(catalyst/exposure/state).

이 저장소에는 '호재' 가 두 갈래로 있었고 서로 이어져 있지 않았다.

    catalyst/assemble.py  → future_catalyst   화면 표시용. 점수에 안 들어간다.
    features/catalyst.py  → catalyst_alpha    **채점에 들어가는 쪽.**
                                              catalyst·catalyst_exposure·catalyst_state
                                              세 표를 읽는데 전부 비어 있었다.

이 모듈이 두 번째 갈래를 채운다. 다섯 항목(§17)을 각각 어디서 가져오는지가
설계의 전부다 — 하나라도 지어내면 그 순간 알파 전체가 감이 된다.

    경제효과    transit_analogue 의 delta (같은 종류 노선에서 관측된 비율 변화)
                → catalyst_state.impact_ratio 로 적고, 단지 가격에 곱해 금액이 된다
    실현확률    단계에서 STAGE_CEILING (착공 0.90 · 공사중 0.95 …)
    시간 적합성 expected_open_ym 과 투자기간 (features/catalyst.py 가 계산)
    노출도      역까지 직선거리 → 800m 안 1.0, 2,000m 에서 0
    선반영률    priced_in.measure() — 발표 이후 이미 벌어진 몫을 **재서** 구한다

── 왜 '같은 종류' 노선의 사례만 쓰는가 ──────────────────────────────
GTX 는 지하철과 다른 물건이다. 도달시간을 줄이는 폭이 다르고 역 간격도 다르다.
그래서 GTX-B 의 참고 사례는 GTX-A 개통분에서만 찾는다. 같은 종류 사례가 없으면
경제효과를 만들지 않는다 — 지하철 평균으로 대신하지 않는다.

── 안 하는 것 ───────────────────────────────────────────────────────
· 개통·운영중 역은 호재로 만들지 않는다. 이미 가격에 있는 것이고, 그 사실은
  future_catalyst 쪽(화면)이 '중립' 으로 이미 보여준다.
· 사례가 한 건뿐이면 그 값을 쓰되 근거에 '사례 1건' 이라고 남긴다. 여러 건이면
  중앙값을 쓴다.
"""
from __future__ import annotations

import json
import sqlite3
import statistics

from apt_engine.catalyst import analogue as analogue_mod
from apt_engine.catalyst import priced_in as priced_in_mod
from apt_engine.catalyst import transit
from apt_engine.trace import Calc

# 아직 안 열린 단계만 호재로 본다.
PLANNED_STAGES = ("계획", "예비타당성", "기본계획", "착공", "공사중", "개통예정")

# 거리에 따른 노출도. 역세권 안은 그대로 받고, 밖으로 나갈수록 선형으로 준다.
# 이 모양은 관측이 아니라 판정 기준이다(STAGE_CEILING 과 같은 성격).
FULL_EXPOSURE_M = transit.NEAR_RADIUS_M     # 800m
ZERO_EXPOSURE_M = transit.FAR_RADIUS_M      # 2,000m


def exposure_of(meters: float) -> float:
    """거리 → 0~1. 800m 안은 1.0, 2,000m 에서 0."""
    if meters <= FULL_EXPOSURE_M:
        return 1.0
    if meters >= ZERO_EXPOSURE_M:
        return 0.0
    span = ZERO_EXPOSURE_M - FULL_EXPOSURE_M
    return round((ZERO_EXPOSURE_M - meters) / span, 4)


def reference_delta(conn: sqlite3.Connection, kind: str) -> tuple[float | None, list[str]]:
    """같은 종류 노선의 개통 사례에서 관측된 비율 변화. (중앙값, 사례 이름들)."""
    rows = conn.execute(
        "SELECT a.station_name, a.delta FROM transit_analogue a "
        "  JOIN transit_station s ON s.id = a.station_id "
        "  JOIN transit_project p ON p.id = s.project_id "
        " WHERE p.kind = ? AND a.delta IS NOT NULL", (kind,)).fetchall()
    if not rows:
        return None, []
    deltas = [float(r["delta"]) for r in rows]
    return statistics.median(deltas), [r["station_name"] for r in rows]


def build(conn: sqlite3.Connection, *, as_of_day: str, area_band: str = "84",
          progress=print) -> dict:
    """미개통 역들을 catalyst/exposure/state 로 만든다."""
    from apt_engine.blind import cutoff as cutoff_mod

    stats = {"stations": 0, "catalysts": 0, "exposures": 0, "skipped": 0}
    # 컷오프에는 보고 지연이 있다(§18). 오늘 날짜로 찍으면 오늘 조회에서
    # 안 보이므로, 관측 가능한 날짜로 찍는다.
    observable = cutoff_mod.AsOf(as_of_day).observable
    state_day = observable.day
    now_ym = observable.ym

    marks = ",".join("?" for _ in PLANNED_STAGES)
    stations = conn.execute(
        f"SELECT s.*, p.name AS project_name, p.kind "
        f"  FROM transit_station s JOIN transit_project p ON p.id = s.project_id "
        f" WHERE s.status IN ({marks}) AND s.lat IS NOT NULL",
        PLANNED_STAGES).fetchall()
    stats["stations"] = len(stations)
    if not stations:
        progress("미개통 역이 없습니다 — `transit import` 로 계획 노선을 넣으세요.")
        return stats

    ref_cache: dict[str, tuple[float | None, list[str]]] = {}

    for st in stations:
        kind = st["kind"]
        if kind not in ref_cache:
            ref_cache[kind] = reference_delta(conn, kind)
        delta, samples = ref_cache[kind]
        if delta is None:
            stats["skipped"] += 1
            progress(f"  건너뜀 {st['name']} — '{kind}' 종류의 개통 사례가 없어 "
                     f"경제효과를 만들 수 없습니다")
            continue

        announced = (st["status_date"] or "")[:4] + (st["status_date"] or "")[5:7]
        priced, priced_calc = (None, None)
        if announced:
            priced, priced_calc = priced_in_mod.measure(
                conn, st, announced_ym=announced, now_ym=now_ym,
                full_delta=delta, area_band=area_band)

        key = f"transit/{st['project_name']}/{st['name']}"
        conn.execute(
            "INSERT INTO catalyst (catalyst_key, catalyst_type, name, lat, lon, "
            " benefit_radius_m, source_name, source_url, note) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(catalyst_key) DO UPDATE SET "
            " lat=excluded.lat, lon=excluded.lon, note=excluded.note",
            (key, _catalyst_type(kind), f"{st['project_name']} {st['name']}",
             st["lat"], st["lon"],
             ZERO_EXPOSURE_M, st["source_name"], st["source_url"],
             f"{st['status']} · 개통예정 {st['expected_open_ym'] or '미상'}"))
        cid = conn.execute("SELECT id FROM catalyst WHERE catalyst_key = ?",
                           (key,)).fetchone()[0]
        stats["catalysts"] += 1

        calc = Calc(
            value=delta, unit="비율",
            formula="같은 종류 노선의 개통 사례에서 관측된 역세권/비역세권 비율 변화",
            inputs={"종류": kind, "사례 수": len(samples)},
            intermediates={"사례": ", ".join(samples[:5]),
                           "중앙값": f"{delta:+.4f}",
                           "주의": "관측된 참고 범위이지 이 역의 예측이 아닙니다"},
            evidence=(analogue_mod.SELF_DERIVED,), grade="ESTIMATED")

        conn.execute(
            # 같은 날 다시 돌리면 덮어쓴다 - 상태는 시점당 하나다.
            "INSERT INTO catalyst_state (catalyst_id, as_of, stage, "
            " announcement_date, expected_completion, realization_probability, "
            " impact_ratio, priced_in_fraction, evidence_json, source_name, "
            " source_url, verification, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(catalyst_id, as_of) DO UPDATE SET "
            " stage=excluded.stage, expected_completion=excluded.expected_completion, "
            " realization_probability=excluded.realization_probability, "
            " impact_ratio=excluded.impact_ratio, "
            " priced_in_fraction=excluded.priced_in_fraction, "
            " evidence_json=excluded.evidence_json, note=excluded.note",
            (cid, state_day, st["status"], st["status_date"],
             st["expected_open_ym"],
             _realization(st["status"]),
             delta, priced,
             json.dumps({"경제효과": calc.to_dict(),
                         "선반영률": priced_calc.to_dict() if priced_calc else None},
                        ensure_ascii=False, default=str),
             st["source_name"], st["source_url"], "ESTIMATED",
             f"경제효과는 {kind} 개통 사례 {len(samples)}건의 중앙값 · "
             f"선반영률은 발표({st['status_date']}) 이후 실측"))

        near = conn.execute(
            "SELECT complex_id, meters FROM station_distance "
            " WHERE station_id = ? AND meters <= ?",
            (st["id"], ZERO_EXPOSURE_M)).fetchall()
        for row in near:
            exp = exposure_of(float(row["meters"]))
            if exp <= 0:
                continue
            conn.execute(
                "INSERT INTO catalyst_exposure (catalyst_id, complex_id, exposure, "
                " meters, method, rationale, as_of) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(catalyst_id, complex_id) DO UPDATE SET "
                " exposure=excluded.exposure, meters=excluded.meters, as_of=excluded.as_of",
                # 노출도는 역 위치가 발표된 날부터 성립하는 사실이다.
                (cid, row["complex_id"], exp, row["meters"], "직선거리",
                 f"{FULL_EXPOSURE_M}m 안 1.0, {ZERO_EXPOSURE_M}m 에서 0 으로 선형 감소",
                 st["status_date"] or state_day))
            stats["exposures"] += 1

    return stats


# catalyst.catalyst_type 은 열거형이라 노선 종류를 그대로 못 쓴다.
# transit_project.kind 는 GTX/지하철/광역철도/일반철도/도로/기타 인데,
# catalyst 쪽은 GTX/지하철/철도/도로/… 라 '철도' 로 모이는 것이 둘이다.
_TYPE_MAP = {"GTX": "GTX", "지하철": "지하철", "광역철도": "철도",
             "일반철도": "철도", "도로": "도로"}


def _catalyst_type(kind: str) -> str:
    return _TYPE_MAP.get(kind, "기타")


# 역의 단계 이름과 STAGE_CEILING 의 단계 이름이 한 군데 다르다.
# 역은 '개통예정', 촉매 표는 '완공예정' 을 쓴다. 같은 뜻이라 맞춰준다.
_STAGE_ALIAS = {"개통예정": "완공예정"}


def _realization(stage: str) -> float | None:
    """단계 → 실현확률. features/catalyst.py 의 STAGE_CEILING 과 같은 표를 쓴다."""
    from apt_engine.features import catalyst as feat
    return feat.STAGE_CEILING.get(_STAGE_ALIAS.get(stage, stage))
