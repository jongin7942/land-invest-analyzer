"""정비사업 단계를 '그 시점에 알 수 있었던 값'으로 — Exit Price 변수용 (MASTER_SPEC §14 Stage Ladder × §12).

서울 정보몽땅·경기데이터드림·인천 renewal 에서 단지에 매칭된 사업의 단계별 일자를 모아,
진입 시점 ym 이하의 최고 단계를 돌려준다(미래 정보 금지). 등록부에 없는 단지는 0(PRE_PROJECT).
단계 사다리: 정비구역지정/추진위 3 · 조합설립 4 · 사업시행인가 5 · 관리처분 6 · 착공 7 · 준공 8 · 안전진단 2.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "rules"
STAGE_COLS = [("준공", 8), ("착공", 7), ("관리처분", 6), ("사업시행인가", 5), ("조합설립", 4), ("추진위", 3), ("정비구역지정", 3), ("안전진단", 2)]


def _ym(v: str | None) -> str | None:
    v = (v or "").strip().replace("-", "").replace(".", "")
    return v[:6] if len(v) >= 6 and v[:6].isdigit() else None


def load() -> dict[int, list[tuple[int, str]]]:
    """complex_id → [(stage, ym)] 오름차순."""
    out: dict[int, list[tuple[int, str]]] = {}
    for name, id_col in (("seoul_redev_matched.csv", "complex_id"), ("gg_redev_matched.csv", "complex_id"), ("incheon_redev_matched.csv", "complex_id")):
        p = RULES / name
        if not p.exists():
            continue
        # 서울 매칭 파일에는 일자가 없고 stages 파일에 있다 → project 로 조인
        stages_by_project: dict[str, dict] = {}
        if name.startswith("seoul"):
            sp = RULES / "seoul_redev_stages.csv"
            if sp.exists():
                with sp.open(encoding="utf-8", newline="") as f:
                    for r in csv.DictReader(f):
                        stages_by_project[r["project"]] = r
        with p.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    cid = int(r[id_col])
                except (TypeError, ValueError):
                    continue
                src = stages_by_project.get(r.get("project", ""), r) if name.startswith("seoul") else r
                for col, stage in STAGE_COLS:
                    ym = _ym(src.get(col)) or _ym(src.get(col + "_approx"))   # 정확 일자 없으면 근사 일자(정보몽땅 추진경과 텍스트)
                    if ym:
                        out.setdefault(cid, []).append((stage, ym))
    for cid in out:
        out[cid].sort(key=lambda x: x[1])
    return out


def stage_at(table: dict[int, list[tuple[int, str]]], cid: int, ym: str) -> int:
    best = 0
    for stage, s_ym in table.get(cid, ()):
        if s_ym <= ym and stage > best:
            best = stage
    return best
