"""전유부 대지권 — 토허 허가대상 판정의 입력 (작업지시서 §3-5).

**왜 이 값이 필요한가**

주거지역 토허 허가대상은 **토지지분 6㎡ 초과**다. 6㎡ 이하면 그 규칙으로는
허가 대상이 아니다. 그래서 이 값 없이는 BLOCK 도 PASS 도 말할 수 없다.

**어디서 오는가**

  VERIFIED   공동주택 공시가격 자료의 전유부 대지권 (부동산공시가격알리미
             열람 · 산정기초자료). 세대·타입별 실제 값이다.
  ESTIMATED  단지 대지면적 × (그 타입 전용면적 / 전체 전유면적합)
             — 우리가 나눈 값이다. 실제 등기 지분과 다를 수 있다.

**추정값은 통과를 만들 수 없다** ← 이 모듈의 핵심

    추정 45㎡  →  "6㎡ 초과" 판정에 쓸 수 있다   (허가대상 = 더 엄격한 쪽)
    추정 5.8㎡ →  "6㎡ 이하" 판정에 **쓸 수 없다** (통과 = 더 느슨한 쪽)

비대칭인 이유: 추정이 틀렸을 때 방향이 다르다. 위쪽으로 틀리면 허가를
받으면 되지만, 아래쪽으로 틀리면 **허가 없이 계약해서 무효가 된다.**
그래서 추정값으로는 절대 문을 열지 않는다.

경계 근처는 추정이 아무리 커도 안 쓴다 — `SAFE_MARGIN` 배 이상이어야
"확실히 초과" 라고 말한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# 공시가격 자료에서 온 값
VERIFIED = "VERIFIED"
# 우리가 대지면적을 나눈 값
ESTIMATED = "ESTIMATED"
UNKNOWN = "UNKNOWN"

# 추정값으로 "초과" 라고 말하려면 기준의 이 배는 넘어야 한다.
# 3배면 6㎡ 기준에 18㎡ — 전용 59㎡ 아파트의 통상 대지권보다 한참 아래라
# 대부분의 단지가 여기 걸리고, 경계 근처만 확인 대상으로 남는다.
SAFE_MARGIN = 3.0


@dataclass(frozen=True)
class LandShare:
    value: float | None
    source: str
    verification: str
    reason: str | None = None
    detail: dict | None = None

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def trustworthy(self) -> bool:
        """문을 열어도 되는 값인가. 추정값은 아니다."""
        return self.known and self.verification == VERIFIED

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        mark = "" if self.verification == VERIFIED else " (추정)"
        return f"{self.value:.1f}㎡{mark} · {self.source}"

    def exceeds(self, threshold: float) -> bool | None:
        """기준을 **초과** 하는가. 모르면 None — 통과시키지 않는다.

        추정값은 넉넉히 큰 경우에만 True 를 낸다. 작다고 False 를 내지는
        않는다 — 그건 문을 여는 방향이라 추정으로 하면 안 된다.
        """
        if not self.known:
            return None
        if self.verification == VERIFIED:
            return self.value > threshold
        if self.value > threshold * SAFE_MARGIN:
            return True          # 확실히 초과 — 더 엄격한 쪽이라 안전하다
        return None              # 경계 근처거나 미만 → 확인해야 안다


def missing(reason: str) -> LandShare:
    return LandShare(None, "-", UNKNOWN, reason)


def load(conn: sqlite3.Connection, *, complex_id: int,
         area_band: str) -> LandShare:
    """저장된 대지권. 없으면 대지면적에서 추정을 시도한다."""
    row = conn.execute(
        "SELECT land_share_m2, land_share_source, exclusive_area_m2 "
        "FROM unit_type WHERE complex_id = ? AND area_band = ? "
        "AND land_share_m2 IS NOT NULL LIMIT 1",
        (complex_id, area_band)).fetchone()
    if row is not None:
        src = row["land_share_source"] or "미상"
        # 출처에 '공시' 가 들어가면 공시가격 자료에서 온 것으로 본다.
        # 그 외(우리가 계산한 것, 출처 미상)는 전부 추정으로 취급한다.
        ver = VERIFIED if "공시" in src else ESTIMATED
        return LandShare(float(row["land_share_m2"]), src, ver)
    return estimate(conn, complex_id=complex_id, area_band=area_band)


def estimate(conn: sqlite3.Connection, *, complex_id: int,
             area_band: str) -> LandShare:
    """단지 대지면적 × (이 타입 전용면적 / 전체 전유면적합).

    등기부상 대지권과 다를 수 있다. 동·향·층에 따라 배분이 달라지고,
    도로·공원 기부채납분이 빠져 있을 수도 있다. 그래서 **추정**이다.
    """
    c = conn.execute(
        "SELECT land_area_m2, land_area_verified FROM complex WHERE id = ?",
        (complex_id,)).fetchone()
    if c is None or c["land_area_m2"] is None:
        return missing("단지 대지면적이 없어 대지권을 추정할 수도 없습니다")

    mine = conn.execute(
        "SELECT exclusive_area_m2, households FROM unit_type "
        "WHERE complex_id = ? AND area_band = ? LIMIT 1",
        (complex_id, area_band)).fetchone()
    if mine is None or not mine["exclusive_area_m2"]:
        return missing(f"{area_band} 타입의 전용면적이 없습니다")

    total = conn.execute(
        "SELECT SUM(exclusive_area_m2 * COALESCE(households, 1)) "
        "FROM unit_type WHERE complex_id = ?", (complex_id,)).fetchone()[0]
    if not total:
        return missing("단지 전체 전유면적합을 구하지 못했습니다")

    share = float(c["land_area_m2"]) * float(mine["exclusive_area_m2"]) / float(total)
    return LandShare(
        share, "단지 대지면적 안분", ESTIMATED,
        detail={
            "단지 대지면적": f"{c['land_area_m2']:,.0f}㎡",
            "이 타입 전용": f"{mine['exclusive_area_m2']:.2f}㎡",
            "전체 전유면적합": f"{total:,.0f}㎡",
            "주의": ("등기부 대지권과 다를 수 있습니다. "
                     "허가대상 '초과' 판정에만 쓰고, '이하' 판정에는 쓰지 않습니다."),
        })


def upsert(conn: sqlite3.Connection, *, complex_id: int, area_band: str,
           land_share_m2: float, source: str) -> None:
    """공시가격 자료에서 읽은 값을 넣는다.

    `source` 에 '공시' 가 들어가야 VERIFIED 로 취급된다 — 출처를 안 적으면
    추정과 구분되지 않고, 구분되지 않으면 문을 여는 데 쓰이게 된다.
    """
    cur = conn.execute(
        "UPDATE unit_type SET land_share_m2 = ?, land_share_source = ? "
        "WHERE complex_id = ? AND area_band = ?",
        (land_share_m2, source, complex_id, area_band))
    if cur.rowcount == 0:
        raise ValueError(
            f"complex_id={complex_id} area_band={area_band} 타입이 없습니다. "
            f"unit_type 을 먼저 만들어야 합니다.")


__all__ = ["LandShare", "load", "estimate", "upsert", "missing",
           "VERIFIED", "ESTIMATED", "UNKNOWN", "SAFE_MARGIN"]
