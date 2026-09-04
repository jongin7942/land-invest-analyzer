"""재건축 단계가 진행되면 정말 값이 오르나 — 재본다.

── 왜 재보는가 ──────────────────────────────────────────────────────
"재건축이 진행되면 오른다" 는 다들 당연하게 말한다. 교통도 그랬다. 그런데
개통 사례 117건을 재보니 중앙값 +0.15%, 오른 사례가 절반이었다(사실상 0).
통념이 측정으로 부정되는 걸 한 번 봤으니, 이번에도 재보기 전에는 믿지 않는다.

다만 미리 말해두면, 이쪽은 교통보다 진짜일 가능성이 높다고 본다. 단계 진행은
**불확실성이 실제로 줄어드는 사건**이기 때문이다. 조합설립인가가 나면 '이
사업이 될까' 라는 물음의 답이 한 칸 확정된다. 개통은 이미 20년 전부터 알려진
일이 예정대로 일어나는 것이라 새 정보가 거의 없다. 그 차이가 결과로 나오는지
보는 것이 이 측정이다.

── 어떻게 재는가 ────────────────────────────────────────────────────
교통에서 쓴 자를 그대로 쓴다. 절대 상승률은 만들지 않는다(요구사항 6).

    그 단지의 대표가격 ÷ 같은 시군구 단지들의 중앙값 = 상대가격

시장 전체의 등락은 분모에서 상쇄된다. 단계 인가일 앞뒤로 이 상대가격이
얼마나 움직였나를 본다.

    상대가격(인가 +12개월) − 상대가격(인가 −12개월)

**같은 단지를 앞뒤로 비교**하므로 구성 변화 문제가 없다(analogue.py 에서
신축 유입에 한 번 데인 자리다). 대신 그 단지에 앞뒤 두 시점의 가격이 다
있어야 하고, 없으면 그 사건은 버린다.

── 이 측정이 답하지 못하는 것 ──────────────────────────────────────
· 단계별 표본이 얇다(조합설립 68건, 사업시행인가 74건이 최대). 중앙값의
  방향은 볼 수 있어도 크기를 정밀하게 말할 수는 없다.
· 경기도만 본다. 서울 자료(정비사업 정보몽땅)는 아직 안 붙였다.
· 이미 진행된 사업만 표본에 있다. 무산된 사업은 애초에 단계 날짜가 없어
  빠지므로, **살아남은 사업만 보는 편향**이 있다. 실제 기대값은 여기서
  나온 값보다 낮다.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.db.connection import get_conn  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "rules" / "gg_redev_matched.csv"
OUT = Path(__file__).resolve().parents[1] / "rules" / "redev_stage_effect.csv"

STAGES = ["정비구역지정", "추진위", "안전진단", "조합설립",
          "사업시행인가", "관리처분", "착공", "준공"]

WINDOW_MONTHS = 12
TOLERANCE = 3               # 그 달에 거래가 없으면 앞뒤 3개월까지 본다
DATA_START_YM = "200610"
MIN_PEERS = 5               # 시군구 중앙값을 낼 최소 단지 수

# 면적대는 단지마다 다르게 고른다.
#
# 처음에는 84㎡ 로 고정했는데 사건 105건이 '가격 없음' 으로 날아갔다. 당연했다 —
# 재건축을 앞둔 1980년대 단지는 59㎡ 이하가 주력이고 84㎡ 거래가 거의 없다.
# 84㎡ 를 요구하는 것은 사실상 '큰 평형이 있는 단지만' 을 고르는 것이고, 그건
# 표본을 줄일 뿐 아니라 한쪽으로 치우치게 만든다.
#
# 그래서 그 단지에서 **스냅샷이 가장 많은 면적대**를 쓴다. 비교 대상인 시군구
# 중앙값도 같은 면적대로 낸다 — 분자와 분모의 면적대가 다르면 비율이 뜻을 잃는다.


def shift_ym(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + int(ym[4:6]) - 1 + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def main_band(conn, complex_id: int) -> str | None:
    """그 단지에서 스냅샷이 가장 많은 면적대."""
    row = conn.execute(
        "SELECT area_band, COUNT(*) n FROM price_snapshot WHERE complex_id = ? "
        " GROUP BY area_band ORDER BY n DESC LIMIT 1", (complex_id,)).fetchone()
    return row["area_band"] if row else None


def price_near(conn, complex_id: int, band: str, ym: str, tol: int = TOLERANCE):
    """그 달 근처의 대표가격. 없으면 None."""
    lo, hi = shift_ym(ym, -tol), shift_ym(ym, tol)
    row = conn.execute(
        "SELECT as_of_ym, representative_price p FROM price_snapshot "
        " WHERE complex_id = ? AND area_band = ? AND as_of_ym BETWEEN ? AND ? "
        " ORDER BY ABS(CAST(as_of_ym AS INTEGER) - ?) LIMIT 1",
        (complex_id, band, lo, hi, int(ym))).fetchone()
    return row["p"] if row else None


def peer_median(conn, lawd_cd: str, band: str, ym: str, exclude: int,
                tol: int = TOLERANCE):
    """같은 시군구·같은 면적대 다른 단지들의 대표가격 중앙값."""
    lo, hi = shift_ym(ym, -tol), shift_ym(ym, tol)
    rows = conn.execute(
        "SELECT ps.complex_id, MAX(ps.representative_price) p "
        "  FROM price_snapshot ps JOIN complex c ON c.id = ps.complex_id "
        " WHERE c.lawd_cd = ? AND ps.area_band = ? AND ps.as_of_ym BETWEEN ? AND ? "
        "   AND ps.complex_id <> ? GROUP BY ps.complex_id",
        (lawd_cd, band, lo, hi, exclude)).fetchall()
    if len(rows) < MIN_PEERS:
        return None, len(rows)
    return statistics.median(r["p"] for r in rows), len(rows)


def relative_at(conn, complex_id: int, lawd_cd: str, band: str, ym: str):
    """시군구 대비 상대가격. 둘 중 하나라도 없으면 None."""
    mine = price_near(conn, complex_id, band, ym)
    if not mine:
        return None
    peers, _ = peer_median(conn, lawd_cd, band, ym, complex_id)
    if not peers:
        return None
    return mine / peers


def main() -> int:
    if not SRC.exists():
        print(f"{SRC} 가 없습니다 — tools/match_gg_redev.py 를 먼저 돌리세요.")
        return 1
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
    print(f"붙은 사업 {len(rows)}건\n")

    results: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    skipped: dict[str, int] = defaultdict(int)

    with get_conn() as conn:
        for r in rows:
            cid, lawd = int(r["complex_id"]), r["lawd_cd"]
            band = main_band(conn, cid)
            if not band:
                skipped["단지에 가격 스냅샷이 없음"] += 1
                continue
            for stage in STAGES:
                day = (r.get(stage) or "").strip()
                if len(day) != 8:
                    continue
                ym = day[:6]
                before, after = shift_ym(ym, -WINDOW_MONTHS), shift_ym(ym, WINDOW_MONTHS)
                if before < DATA_START_YM:
                    skipped[f"{stage}·데이터이전"] += 1
                    continue
                a = relative_at(conn, cid, lawd, band, before)
                b = relative_at(conn, cid, lawd, band, after)
                if a is None or b is None:
                    skipped[f"{stage}·가격없음"] += 1
                    continue
                results[stage].append((b - a, r["complex_name"], day))

    print("═" * 74)
    print(f"단계 인가 전후 {WINDOW_MONTHS}개월, 시군구 대비 상대가격 변화")
    print("═" * 74)
    print(f"  {'단계':14s} {'사례':>4s}  {'중앙값':>9s}  {'상승비율':>8s}  "
          f"{'하위25%':>8s} {'상위25%':>8s}")
    print("  " + "─" * 66)
    table = []
    for stage in STAGES:
        vals = sorted(v for v, _, _ in results[stage])
        if len(vals) < 5:
            print(f"  {stage:14s} {len(vals):4d}  표본이 5건 미만이라 내지 않습니다")
            continue
        med = statistics.median(vals)
        pos = sum(1 for v in vals if v > 0)
        q1, q3 = vals[len(vals) // 4], vals[(len(vals) * 3) // 4]
        print(f"  {stage:14s} {len(vals):4d}  {med:+8.2%}  {pos/len(vals):7.0%}  "
              f"{q1:+8.2%} {q3:+8.2%}")
        table.append({"stage": stage, "samples": len(vals),
                      "median_delta": round(med, 6),
                      "positive_ratio": round(pos / len(vals), 4),
                      "q1": round(q1, 6), "q3": round(q3, 6),
                      "window_months": WINDOW_MONTHS,
                      "source_name": "경기도 일반 정비사업 추진현황 + 자체 가격 스냅샷",
                      "note": "시군구 중앙값 대비 상대가격의 인가 전후 변화"})

    if table:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)
        print(f"\n→ {OUT}")

    print("\n못 잰 사유 (상위)")
    for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])[:10]:
        print(f"   {k:26s} {v:4d}")

    for stage in STAGES:
        vals = sorted(results[stage], key=lambda x: -x[0])
        if len(vals) >= 5:
            print(f"\n{stage} — 상위 3 / 하위 3")
            for v, nm, d in vals[:3]:
                print(f"   {v:+7.2%}  {d}  {nm[:24]}")
            for v, nm, d in vals[-3:]:
                print(f"   {v:+7.2%}  {d}  {nm[:24]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
