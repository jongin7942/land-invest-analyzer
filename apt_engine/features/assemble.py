"""Feature 묶음 만들기 — 한 후보의 모든 Feature 를 한 번에 (Phase 3~6).

여기서 점수를 매기지 않는다. 값과 신뢰도를 모아 `FeatureSet` 으로 넘길 뿐이다.
가중치는 백테스트가 정하고(§74), 이 계층은 그 입력만 만든다.

`REGISTRY` 로 feature 그룹을 이름으로 켜고 끌 수 있다 — §71 Ablation Test 가
"이 factor 를 빼면 성능이 어떻게 되나" 를 물어보기 때문이다.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Iterable

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features import bands
from apt_engine.features import cycle
from apt_engine.features import stretch as stretch_mod
from apt_engine.features import (access, catalyst, entry, flow, jeonse,
                                 momentum, regime, resilience, supply)
from apt_engine.features import relative as relative_mod
from apt_engine.features.base import Feature, FeatureSet

# 그룹 이름 → 그 그룹의 feature 를 만드는 함수.
# Ablation 은 여기 이름으로 그룹을 통째로 끈다.
GROUPS: dict[str, str] = {
    "momentum": "가격 변화율·가속도·늦은발견 (§16·§39·§40)",
    "regime": "시장 국면 7종 (§8)",
    "flow": "거래량 단계·조사우선순위·거래 질 (§15·§16)",
    "supply": "공급 비율·공급 절벽 (§13)",
    "jeonse": "전세가율·하방방어·전세선행 (§14)",
    "entry": "매수가 구간 대비 현재 위치 (§7)",
    "catalyst": "남은 호재 알파 (§17·§18)",
    "access": "역세권 격차가 벌어지는 속도. 수준이 아니라 추세다",
    "resilience": "하락기(2022→2023) 실측 방어력. 방어 전용",
    # DELTA UPGRADE — 4 State 의 CORE 후보. 기존 7그룹을 대체하지 않고 위에 얹는다.
    "bands": "가격대 이동 P25/중앙값/P75 · Latent/Visible · 기울기 지속 (§7·§8·§9)",
    "stretch": "장기 정상가 대비 이탈 · 상승폭 · 가속 구간 (§4-D·§5·§6)",
    "cycle": "과열→회복 사이클 단계 · 가격 도달 경로 (§18·§19)",
    "relative": "비교단지 대비 가격비율이 정상에서 벌어진 정도 (§10)",
}


def build(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
          as_of: cutoff_mod.AsOf, lawd_cd: str | None = None,
          horizon_years: int = 5,
          groups: Iterable[str] | None = None) -> FeatureSet:
    """후보 하나의 Feature 전부. 못 구한 것은 DATA_MISSING 으로 남는다."""
    wanted = set(groups) if groups is not None else set(GROUPS)
    unknown = wanted - set(GROUPS)
    if unknown:
        raise ValueError(f"모르는 feature 그룹: {sorted(unknown)} "
                         f"(가능: {', '.join(sorted(GROUPS))})")

    out = FeatureSet(complex_id, area_band, as_of.day)

    if "momentum" in wanted:
        for f in momentum.all_features(conn, complex_id, area_band, as_of=as_of):
            out = out.add(f)

    if "regime" in wanted:
        if lawd_cd is None:
            row = conn.execute("SELECT lawd_cd FROM complex WHERE id = ?",
                               (complex_id,)).fetchone()
            lawd_cd = row["lawd_cd"] if row else None
        if lawd_cd:
            out = out.add(regime.feature(
                regime.region_regime(conn, lawd_cd, as_of=as_of,
                                     area_band=area_band)))
        else:
            out = out.add(Feature.missing("regime", "시군구를 알 수 없습니다"))

    if "flow" in wanted:
        for f in flow.all_features(conn, complex_id, area_band, as_of=as_of):
            out = out.add(f)

    if "supply" in wanted:
        for f in supply.all_features(conn, complex_id, as_of=as_of):
            out = out.add(f)

    if "jeonse" in wanted:
        for f in jeonse.all_features(conn, complex_id, area_band, as_of=as_of):
            out = out.add(f)

    if "entry" in wanted:
        # 공급이 많으면 매수가를 낮춘다. supply 그룹을 껐으면 조정 없이 간다.
        ratio = out["supply_ratio_3y"].value if "supply_ratio_3y" in out else None
        for f in entry.all_features(conn, complex_id, area_band, as_of=as_of,
                                    supply_ratio=ratio):
            out = out.add(f)

    if "bands" in wanted or "stretch" in wanted or "cycle" in wanted:
        # 두 그룹이 같은 시계열을 쓴다. 한 번만 읽는다.
        series = bands.load_bands(conn, complex_id, area_band, as_of=as_of)
        slopes = bands.slopes(series)

    if "bands" in wanted:
        for f in bands.migration_features(series):
            out = out.add(f)
        out = out.add(bands.slope_persistence(slopes))
        # 전세 바닥이 유지/상승인지는 jeonse 그룹이 알고 있다. 없으면 조건에서 뺀다.
        jeonse_floor = None
        if "jeonse_lead" in out and out["jeonse_lead"].usable:
            jeonse_floor = (out["jeonse_lead"].value or 0) >= 0
        out = out.add(bands.latent_movement(series, slopes,
                                            jeonse_floor_ok=jeonse_floor))
        shift = out["band_shift_strength"]
        volume = (out["investigation_priority"].value
                  if "investigation_priority" in out
                  and out["investigation_priority"].usable else None)
        out = out.add(bands.visible_movement(series, shift,
                                             volume_recovery=volume))
        out = out.add(bands.transaction_recovery(series))
        out = out.add(bands.distribution_exhaustion(series))

    if "stretch" in wanted:
        normal = stretch_mod.historical_normal(series)
        out = out.add(stretch_mod.price_stretch(series, normal))
        for f in stretch_mod.runup_features(series):
            out = out.add(f)
        out = out.add(stretch_mod.acceleration_zone(series, slopes))
        out = out.add(stretch_mod.price_percentile(series))

        # §14 — 얼마나 오래 쌌는가. 정상가를 모르면 세지 않는다.
        from apt_engine.features import leader as leader_mod
        months, _ = stretch_mod.months_cheap(series, normal)
        out = out.add(leader_mod.persistent_cheapness(
            months_cheap=months, gap_closed=None))

        # §4-D — 전세 대비 매매가 괴리. 자기 역사와 비교한다.
        ratio_now = (out["jeonse_ratio"].value
                     if "jeonse_ratio" in out and out["jeonse_ratio"].usable
                     else None)
        history = _jeonse_history(conn, complex_id, area_band, as_of=as_of)
        out = out.add(stretch_mod.price_to_jeonse_stretch(
            None, ratio_now, history))

    if "cycle" in wanted:
        held = None
        if "downside_defense" in out and out["downside_defense"].usable:
            held = (out["downside_defense"].value or 0) >= 0.5
        out = out.add(cycle.reset_feature(
            cycle.excess_reset(series, jeonse_held=held)))
        out = out.add(cycle.path_feature(cycle.price_path(series)))

    if "relative" in wanted:
        out = out.add(relative_mod.relative_gap(
            conn, complex_id, area_band, as_of=as_of))

    if "catalyst" in wanted:
        price = None
        row = conn.execute(
            "SELECT representative_price FROM price_snapshot "
            " WHERE complex_id = ? AND area_band = ? AND as_of_ym <= ? "
            " ORDER BY as_of_ym DESC LIMIT 1",
            (complex_id, area_band, as_of.observable.ym)).fetchone()
        if row and row["representative_price"]:
            price = int(row["representative_price"])
        for f in catalyst.all_features(conn, complex_id, as_of=as_of,
                                       horizon_years=horizon_years, price=price):
            out = out.add(f)

    if "access" in wanted:
        # 컷오프를 따로 보지 않는다. 밴드표는 2008~2025년 전체에서 나온 상수이고,
        # 역까지의 거리는 그 단지가 지어질 때부터 정해진 사실이다.
        for f in access.all_features(conn, complex_id,
                                     horizon_years=horizon_years):
            out = out.add(f)

    if "resilience" in wanted:
        # 2022 고점→2023 저점은 이미 지난 사실이라 컷오프를 따로 보지 않는다.
        # 다만 컷오프가 2023-12 이전이면 그 하락기는 아직 끝나지 않았으므로 세지 않는다.
        if as_of.observable.ym >= resilience.TROUGH_TO:
            for f in resilience.all_features(conn, complex_id, area_band):
                out = out.add(f)

    return out


def group_of(feature_key: str) -> str | None:
    """feature 하나가 어느 그룹에 속하나. Ablation 결과를 그룹 단위로 읽기 위해."""
    prefixes = {
        "momentum": ("momentum_", "price_acceleration", "discovery_lag"),
        "regime": ("regime",),
        "flow": ("flow_stage", "investigation_priority", "transaction_quality"),
        "supply": ("supply_",),
        "jeonse": ("jeonse_", "downside_defense"),
        "entry": ("entry_",),
        "catalyst": ("catalyst_",),
        "access": ("station_access_",),
        "resilience": ("crash_resilience",),
        "bands": ("p25_migration", "median_migration", "p75_migration",
                  "band_shift_strength", "latent_movement", "visible_movement",
                  "slope_persistence", "transaction_recovery",
                  "distribution_exhaustion"),
        "stretch": ("price_stretch", "runup_", "acceleration_zone",
                    "price_percentile", "persistent_cheapness",
                    "price_to_jeonse_stretch"),
        "cycle": ("reset_completion", "path_quality"),
        "relative": ("relative_gap",),
    }
    for group, keys in prefixes.items():
        if any(feature_key.startswith(k) for k in keys):
            return group
    return None


def _jeonse_history(conn, complex_id: int, area_band: str, *,
                    as_of: cutoff_mod.AsOf, months: int = 36) -> list[float]:
    """전세가율 이력. 없으면 빈 목록 — 절대 수준으로 대체하지 않는다."""
    observable = as_of.observable
    end_ym = observable.ym
    total = int(end_ym[:4]) * 12 + int(end_ym[4:6]) - 1 - months
    start_ym = f"{total // 12:04d}{total % 12 + 1:02d}"
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT jeonse_ratio FROM jeonse_snapshot "
            " WHERE complex_id = ? AND area_band = ? "
            "   AND as_of_ym >= ? AND as_of_ym <= ? "
            "   AND jeonse_ratio IS NOT NULL "
            " ORDER BY as_of_ym DESC",
            (complex_id, area_band, start_ym, end_ym)).fetchall()
    return [float(r["jeonse_ratio"]) for r in rows]
