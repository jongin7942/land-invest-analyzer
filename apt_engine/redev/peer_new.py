"""주변 대표 신축 찾기 — 정비사업 완공가치의 기준 (지시서 §24·§27).

**왜 새 API 가 필요 없나**

완공 후 예상가치는 "주변 대표 신축의 미래가격 × 품질 반영률" 이다(§27).
그 '주변 대표 신축' 은 **이미 우리 DB 에 있다.**

    준공연도   K-apt 기본정보 (`complex.approval_year`) — 이미 수집 중
    위치       V-World 지오코딩 (`complex.lat/lon`) — 이미 수집 중
    가격       국토부 실거래 대표가격 (`price_snapshot`) — 이미 수집 중

밖에서 새로 받아올 것이 없다. 사람이 "이 단지 옆 신축은 저기입니다" 라고
넣어 줄 필요도 없다 — 넣게 하면 그 선택 자체가 편향이 된다(§28 정신).

**무엇을 '대표 신축' 으로 보나**

  · 준공 N년 이내                반경 안에서
  · 같은 면적대                   84 짜리 가치를 59 로 재면 안 된다
  · 표본이 충분한 스냅샷          한 건짜리는 대표가 아니다(§49-4)
  · 그중 **가격 상위**            '대표' 는 평균이 아니라 그 동네 최고 상품이다

세대수는 가중치로만 쓴다. 큰 단지가 그 동네 기준이 되는 것은 맞지만,
작아도 비싸면 그게 대표다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apt_engine import geo

# 준공 몇 년 이내를 '신축' 으로 볼 것인가.
NEW_WITHIN_YEARS = 7

# 얼마나 가까워야 '주변' 인가. 생활권이 갈리면 기준이 될 수 없다.
RADIUS_M = 2_000

# 대표가격 스냅샷의 최소 표본. 이 아래는 대표가 아니라 우연이다.
MIN_SAMPLE = 5

# 후보가 이보다 적으면 '대표 신축' 을 정하지 않는다. 하나짜리는 대표가 아니다.
MIN_PEERS = 2


@dataclass(frozen=True)
class PeerNew:
    complex_id: int | None
    name: str | None
    price: int | None
    approval_year: int | None
    distance_m: int | None
    sample_n: int | None
    as_of_ym: str | None
    considered: int = 0
    reason: str | None = None

    @property
    def known(self) -> bool:
        return self.price is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        from apt_engine import units
        return (f"{self.name} ({self.approval_year}년 · {self.distance_m}m) "
                f"{units.fmt_eok(self.price)} · 표본 {self.sample_n}건")


def find(conn: sqlite3.Connection, *, complex_id: int, area_band: str,
         as_of_ym: str, radius_m: int = RADIUS_M,
         new_within_years: int = NEW_WITHIN_YEARS) -> PeerNew:
    """이 단지 주변의 대표 신축.

    좌표가 없으면 **같은 법정동으로 좁혀서** 찾는다. 시군구 전체로 넓히면
    강남구 안에서 압구정 신축을 개포동 재건축의 기준으로 쓰게 된다.
    """
    me = conn.execute(
        "SELECT id, name, lawd_cd, emd_name, lat, lon, approval_year "
        "FROM complex WHERE id = ?", (complex_id,)).fetchone()
    if me is None:
        return PeerNew(None, None, None, None, None, None, None,
                       reason="단지를 찾지 못했습니다")

    year_cut = int(as_of_ym[:4]) - new_within_years
    have_coords = geo.has_coords(me["lat"], me["lon"])

    if have_coords:
        rows = conn.execute(
            "SELECT c.id, c.name, c.lat, c.lon, c.approval_year, "
            "       c.apt_households "
            "FROM complex c "
            "WHERE c.id != ? AND c.approval_year >= ? "
            "  AND c.lat IS NOT NULL AND c.lon IS NOT NULL",
            (complex_id, year_cut)).fetchall()
    else:
        # 좌표가 없으면 같은 법정동까지만. 시군구 전체는 생활권이 다르다.
        rows = conn.execute(
            "SELECT c.id, c.name, c.lat, c.lon, c.approval_year, "
            "       c.apt_households "
            "FROM complex c "
            "WHERE c.id != ? AND c.approval_year >= ? "
            "  AND c.lawd_cd = ? AND c.emd_name IS NOT NULL AND c.emd_name = ?",
            (complex_id, year_cut, me["lawd_cd"], me["emd_name"])).fetchall()

    if not rows:
        where = f"반경 {radius_m}m" if have_coords else "같은 법정동"
        return PeerNew(None, None, None, None, None, None, None,
                       reason=f"{where} 안에 {new_within_years}년 이내 준공 단지가 없습니다")

    best = None
    considered = 0
    for r in rows:
        dist = None
        if have_coords:
            if not geo.has_coords(r["lat"], r["lon"]):
                continue
            dist = geo.haversine_m(me["lat"], me["lon"], r["lat"], r["lon"])
            if dist > radius_m:
                continue

        snap = conn.execute(
            "SELECT representative_price, sample_n, as_of_ym "
            "FROM price_snapshot "
            "WHERE complex_id = ? AND area_band = ? AND as_of_ym <= ? "
            "ORDER BY as_of_ym DESC LIMIT 1",
            (r["id"], area_band, as_of_ym)).fetchone()
        if snap is None or snap["sample_n"] < MIN_SAMPLE:
            continue

        considered += 1
        price = snap["representative_price"]
        if best is None or price > best[0]:
            best = (price, r, dist, snap)

    if considered < MIN_PEERS:
        return PeerNew(None, None, None, None, None, None, None,
                       considered=considered,
                       reason=(f"표본이 충분한 신축이 {considered}개뿐입니다 "
                               f"({MIN_PEERS}개 이상이어야 대표를 정합니다)"))

    price, r, dist, snap = best
    return PeerNew(complex_id=r["id"], name=r["name"], price=price,
                   approval_year=r["approval_year"],
                   distance_m=int(dist) if dist is not None else None,
                   sample_n=snap["sample_n"], as_of_ym=snap["as_of_ym"],
                   considered=considered)


def future_price(peer: PeerNew, *, annual_growth: float | None,
                 years: int) -> dict:
    """대표 신축의 **미래** 가격.

    §27 이 요구하는 것은 현재가가 아니라 완공 시점의 가격이다.
    성장률을 모르면 **지어내지 않는다** — 여기서 임의로 연 3% 같은 값을
    박으면 정비사업 기대수익 전체가 그 가정 위에 서게 된다(§49-16).
    """
    if not peer.known:
        return {"값": None, "사유": peer.reason}
    if annual_growth is None:
        return {"값": None,
                "사유": ("주변 신축의 예상 상승률을 모릅니다. "
                         "임의 값을 넣으면 완공가치 전체가 가정이 됩니다."),
                "현재가": peer.price}
    return {"값": int(peer.price * (1 + annual_growth) ** years),
            "현재가": peer.price,
            "가정": f"연 {annual_growth:+.1%} × {years}년",
            "기준 단지": peer.label,
            "주의": "성장률은 관측이 아니라 가정입니다"}


__all__ = ["PeerNew", "find", "future_price", "NEW_WITHIN_YEARS", "RADIUS_M",
           "MIN_SAMPLE", "MIN_PEERS"]
