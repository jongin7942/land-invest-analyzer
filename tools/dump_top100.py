"""TOP100 을 계산해 reports/top100_latest.json 으로 내보낸다.

`cli rank` 는 화면에 TOP10 세 리스트만 찍고 DB 에도 TOP10 만 저장한다.
100개 전부를 보고서로 만들려면 파이프라인 결과(result.top100)를 직접 받아야
해서, cmd_rank 가 하는 것과 같은 순서로 돌리고 평평한 JSON 으로 적는다.
report_top100.py 가 이 파일을 읽어 HTML 을 만든다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.blind import cutoff as cutoff_mod  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.features import access  # noqa: E402
from apt_engine.invest.budget import Profile  # noqa: E402
from apt_engine.ranking import lists as lists_mod  # noqa: E402
from apt_engine.ranking import pipeline  # noqa: E402
from apt_engine.scoring import weights as weights_mod  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "reports" / "top100_latest.json"


def region_name(conn, lawd_cd: str) -> str:
    """region.name 은 '용인시 기흥구' 꼴이다. 시도는 붙이지 않는다."""
    row = conn.execute("SELECT name FROM region WHERE lawd_cd = ?", (lawd_cd,)).fetchone()
    return row["name"] if row and row["name"] else lawd_cd


def kill_value(kill) -> float:
    for attr in ("score", "value", "total"):
        v = getattr(kill, attr, None)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cash", default="3")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--scan", type=int, default=5000)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--run-key", default=None)
    ap.add_argument("--top", type=int, default=100,
                    help="보고서에 담을 개수 (기본 100). 카톡으로 보낼 땐 10 이 읽기 좋다")
    args = ap.parse_args()

    as_of = cutoff_mod.AsOf(args.as_of or date.today().isoformat())
    # --cash 는 억 단위 (3 = 3억). cli 와 같은 관례다.
    profile = replace(Profile(name="balanced"),
                      available_cash=int(round(float(args.cash) * 1e8)))

    with get_conn() as conn:
        result = pipeline.run(conn, as_of=as_of, profile=profile,
                              horizon_years=args.horizon, scan_limit=args.scan,
                              weights_source=weights_mod.HEURISTIC)
        top = result.top100[:args.top]
        # ★ 는 cli rank 와 같은 기준으로 센다 — TOP10 안에서 세 리스트를 만들고,
        # 그 셋 모두 5위 안(lists.CONVICTION_RANK)이어야 한다. 100개로 리스트를
        # 만들면 위험조정·비대칭 순서가 크게 달라져 아무도 못 든다.
        all_lists = lists_mod.all_lists(result.top10, limit=len(result.top10))
        stars = set(lists_mod.highest_conviction(all_lists))

        rows = []
        for i, c in enumerate(top, 1):
            cx = conn.execute(
                "SELECT name, lawd_cd, approval_year, apt_households FROM complex WHERE id = ?",
                (c.complex_id,)).fetchone()
            near = access.nearest_open_station(conn, c.complex_id)
            drivers = [m for m, _ in c.consensus.top_drivers[:3]]
            rows.append({
                "rank": i, "complex_id": c.complex_id,
                "name": cx["name"], "region": region_name(conn, cx["lawd_cd"]),
                "lawd_cd": cx["lawd_cd"], "approval_year": cx["approval_year"],
                "households": cx["apt_households"], "area_band": c.area_band,
                "price": c.price, "equity": c.required_equity,
                "score": round(c.consensus.score, 2),
                "confidence": round(c.consensus.confidence, 2),
                "agreement": round(c.consensus.agreement, 3),
                "kill": round(kill_value(c.kill), 3),
                "star": c.complex_id in stars,
                "station": near[1] if near else None,
                "station_m": round(near[0]) if near else None,
                "drivers": drivers,
            })

        meta = {
            "as_of": as_of.day, "cash_eok": round(profile.available_cash / 1e8, 1),
            "horizon": args.horizon, "universe": result.universe_size,
            "feasible": len(result.feasible),
            "regime": result.regime,
            "weights_source": result.weights.label,
            "run_key": args.run_key,
            "top": args.top,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"TOP{len(rows)} → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
