"""Leader 망 자동 생성 (신규 지시서 §11·§12·§33).

`leader_link` 테이블을 채우는 계산 경로다. 이게 없으면 EarlyAlpha 의 곱셈 항
둘(RemainingRecoverableGap · TransmissionProbability)이 영원히 비어서 점수가
반쪽으로 나온다.

§11 이 요구한 것:

> 가까운 아파트를 무조건 Leader 로 지정하지 않는다.
> Relevant Leader 는 실제 Buyer Overlap 을 기준으로 선정한다.

⚠ **Buyer Overlap 은 대리지표다.** 진짜로 재려면 "이 단지를 본 사람이 저 단지도
봤는가" 라는 행동 데이터가 필요한데 그런 건 없다. 그래서 관측 가능한 것으로
근사한다.

    같은 생활권인가          같은 동네를 보는 사람은 겹친다
    가격대가 겹치는가        예산이 다르면 후보군도 다르다
    같은 면적대인가          59㎡ 찾는 사람이 84㎡ 를 사지 않는다

셋 다 관측되고, 셋 다 이름이 아니라 숫자다(§33). 근사값이라 신뢰도에 상한을
두고 `overlap_basis` 에 무엇으로 쟀는지 적는다.

§11 이 정한 Leader 다섯 종류를 각각 다른 규칙으로 뽑는다. 한 규칙으로 다섯 개를
만들면 이름만 다섯 개지 실제로는 하나다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.leader import (CAPITAL_COHORT, FLOW, LEADER_KINDS,
                                        LOCAL, METRO, PRICE)

# Leader 는 Follower 보다 이만큼은 비싸야 한다. 비슷하면 선도가 아니라 동급이다.
MIN_PRICE_LEAD = 0.10
# 너무 비싸면 다른 시장이다. 5억짜리를 보는 사람이 20억을 보지 않는다.
MAX_PRICE_LEAD = 1.50

# 겹침 구성요소의 가중치. **판정 기준이지 관측이 아니다.**
OVERLAP_WEIGHTS = {"생활권": 0.45, "가격대": 0.35, "면적대": 0.20}

# 대리지표라서 겹침 신뢰도에 상한을 건다.
MAX_PROXY_OVERLAP = 0.80

OVERLAP_NOTE = ("Buyer Overlap 은 행동 데이터가 없어 생활권·가격대·면적대로 "
                "근사한 값입니다. 실측이 아니라 상한을 둡니다")


@dataclass(frozen=True)
class Node:
    complex_id: int
    price: int
    lawd_cd: str
    life_zone: str | None
    area_band: str
    sample_n: int

    @property
    def known(self) -> bool:
        return self.price > 0


@dataclass(frozen=True)
class Link:
    follower_id: int
    leader_id: int
    area_band: str
    kind: str
    overlap: float
    basis: str
    evidence: dict = field(default_factory=dict)


def load_nodes(conn: sqlite3.Connection, *, as_of: cutoff_mod.AsOf,
               area_band: str) -> list[Node]:
    """그 시점에 가격을 알 수 있었던 단지 전부. 이름은 읽지 않는다(§1)."""
    observable = as_of.observable
    end_ym = _shift(observable.ym, -1)
    start_ym = _shift(end_ym, -11)
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT s.complex_id, s.representative_price, s.sample_n, "
            "       c.lawd_cd, c.life_zone "
            "  FROM price_snapshot s JOIN complex c ON c.id = s.complex_id "
            " WHERE s.area_band = ? AND s.as_of_ym >= ? AND s.as_of_ym <= ? "
            "   AND c.canonical_id IS NULL "
            " ORDER BY s.complex_id, s.as_of_ym DESC",
            (area_band, start_ym, end_ym)).fetchall()
    seen: set[int] = set()
    out: list[Node] = []
    for r in rows:
        cid = int(r["complex_id"])
        if cid in seen or not r["representative_price"]:
            continue
        seen.add(cid)
        out.append(Node(cid, int(r["representative_price"]), r["lawd_cd"] or "",
                        r["life_zone"], area_band, int(r["sample_n"] or 0)))
    return out


def buyer_overlap(follower: Node, leader: Node) -> tuple[float, str]:
    """겹침 대리지표 (§11).

    셋을 각각 0~1 로 재고 가중평균한다. 하나도 못 재면 0 이 아니라
    **호출부가 Leader 로 인정하지 않게** 낮은 값이 나온다.
    """
    parts: dict[str, float] = {}

    # 생활권 — 같은 생활권 > 같은 시군구 > 그 외
    if follower.life_zone and leader.life_zone:
        parts["생활권"] = 1.0 if follower.life_zone == leader.life_zone else 0.2
    elif follower.lawd_cd and leader.lawd_cd:
        parts["생활권"] = 0.7 if follower.lawd_cd == leader.lawd_cd else 0.15

    # 가격대 — 가까울수록 후보군이 겹친다
    if follower.price > 0 and leader.price > 0:
        ratio = leader.price / follower.price
        if ratio < 1.0:
            parts["가격대"] = 0.0          # Leader 가 더 싸면 선도가 아니다
        elif ratio <= 1.30:
            parts["가격대"] = 1.0
        elif ratio <= MAX_PRICE_LEAD:
            parts["가격대"] = max(0.0, 1.0 - (ratio - 1.30) / 0.20)
        else:
            parts["가격대"] = 0.0

    # 면적대 — 59 를 찾는 사람이 84 를 사지 않는다
    parts["면적대"] = 1.0 if follower.area_band == leader.area_band else 0.0

    if not parts:
        return 0.0, "겹침을 잴 재료가 없습니다"

    total_w = sum(OVERLAP_WEIGHTS[k] for k in parts)
    value = sum(parts[k] * OVERLAP_WEIGHTS[k] for k in parts) / total_w
    basis = " · ".join(f"{k} {parts[k]:.2f}" for k in sorted(parts))
    # 상한을 **곱으로** 건다. min() 으로 자르면 상한 위쪽의 서로 다른 값들이
    # 전부 같아져서 "면적이 달라도 겹침이 같다" 가 된다.
    return value * MAX_PROXY_OVERLAP, basis


def _in_lead_range(follower: Node, leader: Node) -> bool:
    if leader.complex_id == follower.complex_id or not follower.price:
        return False
    ratio = leader.price / follower.price
    return 1 + MIN_PRICE_LEAD <= ratio <= MAX_PRICE_LEAD


def pick_leaders(follower: Node, nodes: list[Node]) -> list[Link]:
    """다섯 종류를 각각 다른 규칙으로 뽑는다 (§11).

    한 규칙으로 다섯 개를 만들면 이름만 다섯 개지 실제로는 하나다.
    """
    pool = [n for n in nodes if _in_lead_range(follower, n)]
    if not pool:
        return []

    picks: dict[str, Node] = {}

    same_zone = [n for n in pool
                 if (follower.life_zone and n.life_zone == follower.life_zone)
                 or (not follower.life_zone and n.lawd_cd == follower.lawd_cd)]
    if same_zone:
        picks[LOCAL] = max(same_zone, key=lambda n: n.price)

    # 바로 위 가격대 — 가장 비싼 게 아니라 **가장 가까이 위**
    above = sorted(pool, key=lambda n: n.price)
    if above:
        picks[PRICE] = above[0]

    flow_pool = [n for n in pool if n.sample_n > 0]
    if flow_pool:
        picks[FLOW] = max(flow_pool, key=lambda n: n.sample_n)

    # 같은 자본 코호트에서 한 단계 위 (§24) — 가격 1.1~1.3배 구간의 최고
    cohort = [n for n in pool if 1.10 <= n.price / follower.price <= 1.30]
    if cohort:
        picks[CAPITAL_COHORT] = max(cohort, key=lambda n: n.price)

    if nodes:
        top = max(nodes, key=lambda n: n.price)
        if _in_lead_range(follower, top):
            picks[METRO] = top

    links: list[Link] = []
    for kind, leader in picks.items():
        overlap, basis = buyer_overlap(follower, leader)
        links.append(Link(
            follower.complex_id, leader.complex_id, follower.area_band, kind,
            overlap, basis,
            {"follower_price": follower.price, "leader_price": leader.price,
             "ratio": round(leader.price / follower.price, 4),
             "주의": OVERLAP_NOTE}))
    return links


def build(conn: sqlite3.Connection, *, as_of: cutoff_mod.AsOf,
          area_band: str, limit: int | None = None) -> dict:
    """Leader 망을 만들어 저장한다.

    같은 시점에 다시 돌리면 덮어쓴다(UNIQUE 로 막혀 있으므로 UPSERT).
    **다른 시점의 행은 건드리지 않는다** — 과거 시점 백테스트가 그때의 망을
    그대로 봐야 하기 때문이다.

    저장하는 `as_of` 는 **관측 가능 시점**(신고지연 반영)이다. 요청받은 날짜로
    저장하면 읽는 쪽이 컷오프를 걸었을 때 자기가 방금 쓴 행을 못 본다 —
    쓸 때는 2024-06-01, 읽을 때는 2024-05-02 이하라서 항상 밖이다.
    실제로 링크 109개를 쓰고 하나도 못 읽는 것을 봤다.
    """
    stamp = as_of.observable.day
    nodes = load_nodes(conn, as_of=as_of, area_band=area_band)
    if not nodes:
        return {"단지": 0, "링크": 0,
                "사유": f"{as_of.day} 시점에 {area_band}㎡ 가격 스냅샷이 없습니다"}

    targets = nodes[:limit] if limit else nodes
    written = 0
    for follower in targets:
        for link in pick_leaders(follower, nodes):
            conn.execute(
                "INSERT INTO leader_link (follower_id, leader_id, area_band, "
                " leader_kind, as_of, buyer_overlap, overlap_basis, "
                " evidence_json) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(follower_id, leader_id, area_band, leader_kind, "
                " as_of) DO UPDATE SET buyer_overlap=excluded.buyer_overlap, "
                " overlap_basis=excluded.overlap_basis, "
                " evidence_json=excluded.evidence_json",
                (link.follower_id, link.leader_id, link.area_band, link.kind,
                 stamp, link.overlap, link.basis,
                 json.dumps(link.evidence, ensure_ascii=False)))
            written += 1
    return {"단지": len(targets), "링크": written, "as_of": stamp,
            "종류": list(LEADER_KINDS), "주의": OVERLAP_NOTE}


def _shift(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"
