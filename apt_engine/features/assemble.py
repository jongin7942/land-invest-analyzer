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
from apt_engine.features import (catalyst, entry, flow, jeonse, momentum,
                                 regime, supply)
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
    }
    for group, keys in prefixes.items():
        if any(feature_key.startswith(k) for k in keys):
            return group
    return None
