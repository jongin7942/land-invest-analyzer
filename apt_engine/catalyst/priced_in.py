"""선반영률 — "이미 얼마나 가격에 들어갔나" 를 재서 구한다.

Catalyst Alpha 는 다섯 항목의 곱인데(§17), 그중 넷은 사실이나 관측값에서 온다.

    경제효과      transit_analogue — 이미 개통한 역에서 관측된 비율 변화
    실현확률      단계(착공·공사중…)에서 STAGE_CEILING 으로
    시간 적합성   개통 예정시점과 투자기간의 관계
    노출도        역까지의 거리

남은 하나가 **선반영률**이다. 이건 "지금 사서 얻을 게 남았나" 를 가르는 항목이라
가장 중요한데, 동시에 가장 지어내기 쉬운 항목이다. "GTX 는 절반쯤 반영됐겠지"
같은 감으로 0.5 를 적으면 그 순간 알파 전체가 그 감에 얹힌다.

── 재는 방법 ────────────────────────────────────────────────────────
개통 사례에서 쓰는 것과 같은 자를 쓴다. 역세권/비역세권 대표가격 **비율**이
얼마나 움직였나. 시장 전체의 등락은 분자·분모에서 상쇄된다.

    발표 시점의 비율   역세권/비역세권 = 1.02
    지금의 비율        역세권/비역세권 = 1.06
    → 발표 이후 +4%p 만큼 이미 벌어졌다

이걸 완공까지 갈 것으로 보는 전체 폭(개통 사례에서 관측된 delta)과 견준다.

    선반영률 = 발표 이후 벌어진 폭 / 개통 사례에서 관측된 전체 폭

전체 폭이 +8%p 인데 이미 +4%p 벌어졌으면 선반영률 0.5 다. 남은 것은 절반이다.

── 이 방법의 한계 ──────────────────────────────────────────────────
· 개통 사례가 그 노선의 미래를 그대로 말해주지 않는다. GTX-A 킨텍스에서 +2.1%p
  였다고 GTX-B 부평이 같으리란 보장은 없다. 그래서 '참고 범위' 이지 예측이 아니다.
· 발표 이후 그 지역에 다른 일(재개발·공급)이 있었으면 그 몫이 섞인다. 비역세권
  대조군이 같은 시군구라 상당 부분 상쇄되지만 전부는 아니다.
· 이미 전체 폭을 넘게 벌어졌으면(선반영률 > 1) 1.0 으로 자른다. 남은 알파는 0 이다.
  "이미 다 반영됐다" 는 뜻이지 "마이너스 알파" 라는 뜻이 아니다 — 그 판단은
  이 함수가 할 일이 아니다.
"""
from __future__ import annotations

import sqlite3
import statistics

from apt_engine.catalyst import analogue as analogue_mod
from apt_engine.catalyst import transit
from apt_engine.trace import Calc

MIN_SAMPLES = analogue_mod.MIN_SAMPLES


def ratio_at(conn: sqlite3.Connection, station_id: int, lawd_cd: str | None, *,
             area_band: str, ym: str,
             radius_m: int = transit.NEAR_RADIUS_M) -> tuple[float | None, int, int]:
    """그 달의 역세권/비역세권 대표가격 비율. (비율, 역세권 표본, 비역세권 표본)."""
    near, far = analogue_mod._split_by_distance(conn, station_id, lawd_cd, radius_m)
    if len(near) < MIN_SAMPLES or len(far) < MIN_SAMPLES:
        return None, len(near), len(far)
    n_med, n_n = analogue_mod._median_price(conn, near, area_band, ym)
    f_med, f_n = analogue_mod._median_price(conn, far, area_band, ym)
    if not n_med or not f_med or min(n_n, f_n) < MIN_SAMPLES:
        return None, n_n, f_n
    return n_med / f_med, n_n, f_n


def measure(conn: sqlite3.Connection, station_row: sqlite3.Row, *,
            announced_ym: str, now_ym: str, full_delta: float,
            area_band: str = "84") -> tuple[float | None, Calc]:
    """발표 이후 이미 벌어진 몫 ÷ 전체 폭 = 선반영률.

    `full_delta` 는 개통 사례(transit_analogue)에서 관측된 전체 폭이다.
    이게 0 이하면 선반영률을 만들지 않는다 — 나눌 기준이 없다.
    """
    inputs = {"역": station_row["name"], "발표": announced_ym, "현재": now_ym,
              "전체 폭(개통 사례)": f"{full_delta:+.3f}"}

    if full_delta <= 0:
        return None, Calc(
            value=None, unit="",
            formula="선반영률 = 발표 이후 벌어진 폭 ÷ 개통 사례 전체 폭",
            inputs=inputs,
            intermediates={"사유": "개통 사례의 전체 폭이 0 이하라 나눌 기준이 없습니다"},
            grade="SCENARIO")

    then, n1, f1 = ratio_at(conn, station_row["id"], station_row["lawd_cd"],
                            area_band=area_band, ym=announced_ym)
    now, n2, f2 = ratio_at(conn, station_row["id"], station_row["lawd_cd"],
                           area_band=area_band, ym=now_ym)
    inputs["표본"] = f"발표 {n1}/{f1} · 현재 {n2}/{f2}"

    if then is None or now is None:
        return None, Calc(
            value=None, unit="",
            formula="선반영률 = 발표 이후 벌어진 폭 ÷ 개통 사례 전체 폭",
            inputs=inputs,
            intermediates={"사유": "그 시점의 역세권/비역세권 표본이 모자랍니다 "
                                  f"(최소 {MIN_SAMPLES}단지씩)"},
            grade="SCENARIO")

    moved = now - then
    raw = moved / full_delta
    priced = max(0.0, min(1.0, raw))

    note = {}
    if raw > 1.0:
        note["주의"] = ("발표 이후 벌어진 폭이 개통 사례 전체 폭을 이미 넘었습니다. "
                       "1.0 으로 잘랐습니다 — 남은 알파는 0 입니다")
    elif raw < 0:
        note["주의"] = ("발표 이후 오히려 좁혀졌습니다. 0 으로 잘랐습니다 — "
                       "호재가 마이너스로 작동했다고 단정하지 않습니다")

    return priced, Calc(
        value=priced, unit="",
        formula="선반영률 = (현재 비율 − 발표시점 비율) ÷ 개통 사례 전체 폭",
        inputs=inputs,
        intermediates={
            "발표시점 비율": f"{then:.4f}",
            "현재 비율": f"{now:.4f}",
            "벌어진 폭": f"{moved:+.4f}",
            "선반영률": f"{priced:.0%}",
            **note,
        },
        evidence=(analogue_mod.SELF_DERIVED,),
        grade="ESTIMATED")
