"""용적률 재산정 (종인님 지적 2026-09-05: 부평 동아1단지 219.5% → 실제 181%).

원인: complex.gross_floor_area_m2 에 K-apt `kaptTarea`(관리비부과면적, 지하주차장·부속시설 포함)가 들어가 있고,
용적률 = 그 값 ÷ V-World 대표 필지 면적 이라 **지상 연면적만 쓰는 용적률보다 항상 크게** 나온다. 지하주차장이 큰
신축일수록 오차가 크다(예: 동암 신동아 434%).

대안 추정: K-apt `privArea`(주거전용면적 합) × 1.15 ÷ 대지면적. 1.15 는 동아1단지 실측(181%)으로 맞춘 전용→용적률 산정
연면적 배율(전용률 87%)이며, 계단식 구축에서 신축으로 갈수록 실제 배율은 1.2~1.3 까지 커진다 → PROXY 로 표기.
`rules/far_overrides.csv` 에 있는 단지는 그 값을 쓴다(VERIFIED 외부 표기).

저장: complex_attribute(priv_area_m2, kapt_marea_m2, far_est_priv, far_prev_tarea) + complex.current_far 갱신.
    .venv/Scripts/python.exe tools/refit_far.py [--limit N] [--dry]
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.collectors import kapt  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402

K = 1.15
SOURCE_TIER = __import__("sqlite3").connect(str(ROOT / "apt_invest.db")).execute("SELECT MIN(tier) FROM source_tier").fetchone()[0] or 1
SRC = "K-apt privArea × 1.15 ÷ V-World 대지 (PROXY, tools/refit_far.py)"


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    overrides = {int(r["complex_id"]): r for r in csv.DictReader((ROOT / "rules" / "far_overrides.csv").open(encoding="utf-8"))}
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, kapt_code, land_area_m2, gross_floor_area_m2, current_far FROM complex "
                            "WHERE canonical_id IS NULL AND apt_households >= 1000 AND kapt_code IS NOT NULL ORDER BY id").fetchall()
    if a.limit:
        rows = rows[: a.limit]
    import json
    cache_p = ROOT / "logs" / "_kapt_priv_cache.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    t0 = time.time(); done = fail = 0; changes = []
    for i, r in enumerate(rows, 1):
        if r["kapt_code"] in cache:
            raw = cache[r["kapt_code"]]
        else:
            try:
                b = kapt.fetch_basis(r["kapt_code"])
            except Exception as e:  # noqa: BLE001
                b = None
            raw0 = (b or {}).get("raw") or {}
            raw = {"privArea": raw0.get("privArea"), "kaptMarea": raw0.get("kaptMarea"), "kaptTarea": raw0.get("kaptTarea"), "kaptdPcntu": raw0.get("kaptdPcntu")}
            cache[r["kapt_code"]] = raw
            if i % 50 == 0:
                cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        priv = raw.get("privArea"); marea = raw.get("kaptMarea")
        try:
            priv = float(priv) if priv not in (None, "") else None
            marea = float(marea) if marea not in (None, "") else None
        except ValueError:
            priv = marea = None
        if priv is None:
            fail += 1
        else:
            done += 1
        land = r["land_area_m2"]
        far_est = (priv * K / land * 100.0) if (priv and land) else None
        ov = overrides.get(r["id"])
        new_far = float(ov["current_far"]) if ov else far_est
        changes.append((r["id"], priv, marea, far_est, r["current_far"], new_far, bool(ov)))
        if i % 100 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)} priv 확보 {done} 실패 {fail} ({time.time()-t0:.0f}s)", flush=True)
    cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if a.dry:
        for c in changes[:20]:
            print(c)
        return 0
    with get_conn() as conn:
        conn.execute("DELETE FROM complex_attribute WHERE attr_key IN ('priv_area_m2','kapt_marea_m2','far_est_priv','far_prev_tarea')")
        for cid, priv, marea, far_est, far_prev, new_far, is_ov in changes:
            for key, val, unit in (("priv_area_m2", priv, "㎡"), ("kapt_marea_m2", marea, "㎡"), ("far_est_priv", far_est, "%"), ("far_prev_tarea", far_prev, "%")):
                if val is not None:
                    conn.execute("INSERT OR REPLACE INTO complex_attribute(complex_id, attr_key, value_num, unit, as_of, source_name, source_tier, confidence, verification, note) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (cid, key, float(val), unit, "2026-09-05", "K-apt 기본정보" if key in ("priv_area_m2", "kapt_marea_m2") else SRC, SOURCE_TIER,
                                  "MEDIUM" if key == "far_est_priv" else ("LOW" if key == "far_prev_tarea" else "HIGH"),
                                  "VERIFIED" if key in ("priv_area_m2", "kapt_marea_m2") else "ESTIMATED",
                                  "지하 포함 연면적(kaptTarea) 기반 과대 추정값 — 참고용" if key == "far_prev_tarea" else None))
            if new_far is not None:
                conn.execute("UPDATE complex SET current_far = ?, land_area_source = CASE WHEN land_area_source LIKE '%FAR override%' THEN land_area_source ELSE COALESCE(land_area_source,'') || ' | ' || ? END, "
                             "updated_at = datetime('now','localtime') WHERE id = ?", (new_far, "FAR override(외부 표기)" if is_ov else SRC, cid))
        conn.commit()
    ratio = [c[3] / c[4] for c in changes if c[3] and c[4]]
    ratio.sort()
    print(f"완료: {len(changes)}단지 · far_est 산출 {sum(1 for c in changes if c[3])} · 기존/신규 비율 중앙 {ratio[len(ratio)//2]:.3f} (p10 {ratio[len(ratio)//10]:.3f} p90 {ratio[len(ratio)*9//10]:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
