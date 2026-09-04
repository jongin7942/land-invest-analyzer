"""정비사업 Option Stage Registry — MASTER_SPEC §14 (Option Value Engine) 의 첫 구현.

서울 정보몽땅 · 경기데이터드림 · 인천 renewal 에서 단지에 매칭된 정비사업 기록을
MASTER_SPEC §14.2 의 Stage Ladder(0~8) 로 옮기고, §14.10 필수 출력 컬럼을 채운다.
계산하지 않은 값은 전부 N/A 로 둔다 — 임의 숫자를 넣지 않는다(§14.1).

    실행:  .venv/Scripts/python.exe tools/build_option_stage_registry.py
    출력:  rules/option_stage_registry.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402

RULES = ROOT / "rules"
OUT = RULES / "option_stage_registry.csv"
AS_OF_YEAR = 2026
MIN_AGE_YEARS = 30          # 재건축 연한(정비사업 Eligibility 의 최소 연식). 점수가 아니라 등재 범위다

# ── §14.2 Stage Ladder ─────────────────────────────────────────────────
LABELS = {0: "PRE_PROJECT", 1: "POLICY_ELIGIBLE", 2: "EARLY_PROJECT", 3: "FORMAL_ENTRY",
          4: "OPERATOR_FORMED", 5: "PROJECT_APPROVED", 6: "DISPOSITION_APPROVED",
          7: "CONSTRUCTION", 8: "NEAR_COMPLETE"}

# 지자체 라벨 → 사다리 칸. 순서가 중요하다: 앞에서 걸리는 것이 우선.
LABEL_RULES: list[tuple[tuple[str, ...], int]] = [
    (("준공", "이전고시", "해산", "청산", "완료"), 8),
    (("착공", "이주", "철거", "분양"), 7),
    (("관리처분", "분양신청"), 6),
    (("사업시행",), 5),
    (("조합설립", "시행자지정", "신탁", "건축심의", "교통심의"), 4),
    (("추진위", "정비구역지정", "구역지정", "정비계획", "안전진단통과", "정비구역"), 3),
    (("안전진단", "준비위", "동의서", "설명회", "입안제안", "모집신고"), 2),
    (("예정구역", "정비예정", "기본계획", "노후계획도시", "역세권"), 1),
]
DATE_COLS: list[tuple[str, int]] = [
    ("준공", 8), ("착공", 7), ("관리처분", 6), ("사업시행인가", 5), ("조합설립", 4),
    ("추진위", 3), ("정비구역지정", 3), ("안전진단", 2), ("정비예정구역고시일자", 1),
]

OUTPUT_COLS = [
    "complex_id", "complex_name", "lawd_cd", "region", "project_name", "project_type",
    "option_stage", "option_stage_label", "stage_verification", "stage_source", "stage_label_raw",
    "match_meters",
    "existing_far", "existing_households", "site_area", "land_share",
    "base_allowed_far", "policy_allowed_far", "upside_allowed_far",
    "estimated_new_households", "member_households", "general_sale_units", "general_sale_ratio",
    "estimated_member_contribution", "contribution_status",
    "years_to_next_stage", "years_to_completion",
    "project_probability", "probability_status",
    "base_case_liquid_exit", "project_case_liquid_exit", "upside_case_liquid_exit",
    "base_terminal_wealth", "project_terminal_wealth", "upside_terminal_wealth",
    "net_project_upside", "option_value", "option_already_priced_ratio",
    "option_data_confidence", "option_research_status", "option_research_priority",
]
NA = "N/A"


def stage_from_label(label: str) -> int | None:
    s = (label or "").replace(" ", "")
    if not s:
        return None
    for keys, stage in LABEL_RULES:
        if any(k in s for k in keys):
            return stage
    return None


def stage_from_dates(row: dict) -> int:
    best = -1
    for col, stage in DATE_COLS:
        v = (row.get(col) or "").strip()
        if v and v not in ("-", "0"):
            best = max(best, stage)
    return best


def _read(name: str) -> list[dict]:
    p = RULES / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _num(v) -> str:
    v = (v or "").strip().rstrip("%")
    try:
        return str(float(v)) if v else NA
    except ValueError:
        return NA


def build() -> list[dict]:
    rows: dict[int, dict] = {}

    def put(cid: int, name: str, lawd: str, region: str, project: str, ptype: str,
            label: str, stage_l: int | None, stage_d: int, meters: str, source: str,
            existing_far=NA, households=NA, site_area=NA, planned_far=NA,
            general_units=NA, member_units=NA, new_units=NA):
        stage = max(stage_l if stage_l is not None else -1, stage_d)
        if stage < 0:
            stage, verification = 0, "NO_STAGE_EVIDENCE_IN_RECORD"
        else:
            # 공식 등록부(정보몽땅·경기데이터드림·인천 renewal) → 단계는 VERIFIED.
            # 단지-사업 매칭이 멀면(>150m) 매칭 자체가 PROXY.
            try:
                verification = "VERIFIED" if float(meters or 0) <= 150 else "PROXY_MATCH"
            except ValueError:
                verification = "PROXY_MATCH"
        prev = rows.get(cid)
        if prev and int(prev["option_stage"]) >= stage:
            return
        gsr = NA
        if general_units != NA and households != NA:
            try:
                gsr = f"{float(general_units) / float(households):.3f}"
            except (ValueError, ZeroDivisionError):
                gsr = NA
        rows[cid] = {
            "complex_id": cid, "complex_name": name, "lawd_cd": lawd, "region": region,
            "project_name": project, "project_type": ptype,
            "option_stage": stage, "option_stage_label": LABELS[stage],
            "stage_verification": verification, "stage_source": source,
            "stage_label_raw": label, "match_meters": meters,
            "existing_far": existing_far, "existing_households": households,
            "site_area": site_area, "land_share": NA,
            "base_allowed_far": NA, "policy_allowed_far": NA,
            "upside_allowed_far": NA,
            "estimated_new_households": new_units, "member_households": member_units,
            "general_sale_units": general_units, "general_sale_ratio": gsr,
            "estimated_member_contribution": NA, "contribution_status": "NOT_CALCULATED",
            "years_to_next_stage": NA, "years_to_completion": NA,
            "project_probability": NA, "probability_status": "UNKNOWN",
            "base_case_liquid_exit": NA, "project_case_liquid_exit": NA,
            "upside_case_liquid_exit": NA,
            "base_terminal_wealth": NA, "project_terminal_wealth": NA,
            "upside_terminal_wealth": NA,
            "net_project_upside": NA, "option_value": "NOT_CALCULATED",
            "option_already_priced_ratio": NA,
            "option_data_confidence": "LOW" if verification != "VERIFIED" else "MEDIUM",
            "option_research_status": "STAGE_MAPPED_ONLY",
            "option_research_priority": "HIGH" if stage >= 2 else ("MEDIUM" if stage == 1 else "LOW"),
        }
        # 정비계획에 신축 용적률이 있으면 그것이 BASE(정비계획 확정값)다 — §14.4
        if planned_far != NA:
            rows[cid]["base_allowed_far"] = planned_far

    for r in _read("seoul_redev_matched.csv"):
        put(int(r["complex_id"]), r["complex_name"], r["lawd_cd"], "서울", r["project"],
            r["biz_type"], r["stage"], stage_from_label(r["stage"]), -1, r.get("meters", ""),
            "서울 정비사업 정보몽땅")
    for r in _read("gg_redev_matched.csv"):
        put(int(r["complex_id"]), r["complex_name"], r["lawd_cd"], "경기", r["zone_name"],
            r["biz_type"], r["biz_step"], stage_from_label(r["biz_step"]), stage_from_dates(r),
            r.get("match_meters", ""), "경기데이터드림 정비사업 추진현황",
            existing_far=_num(r.get("existing_far")), households=_num(r.get("existing_households")),
            planned_far=_num(r.get("planned_far")))
    for r in _read("incheon_redev_matched.csv"):
        put(int(r["complex_id"]), r["complex_name"], r["lawd_cd"], "인천", r["zone"],
            r["biz_type"], r["stage_now"], stage_from_label(r["stage_now"]), stage_from_dates(r),
            r.get("meters", ""), "인천 renewal 정비사업 현황", households=_num(r.get("households")))

    # 공식 기록이 없는 노후 단지(사용승인 30년 이상)는 전부 Stage 0 — 근거 없음을 그대로 적는다.
    # 기존 선별의 용적률 200% 컷오프는 쓰지 않는다(§14.4: 낮은 용적률은 입력값이지 Gate 가 아니다).
    with get_conn() as conn:
        region_of = {"11": "서울", "28": "인천", "41": "경기"}
        for cid, name, lawd, far, hh, ls in conn.execute(
                "SELECT c.id, c.name, c.lawd_cd, c.current_far, c.apt_households, "
                "       CASE WHEN c.land_area_m2 > 0 AND c.apt_households > 0 "
                "            THEN c.land_area_m2 * 1.0 / c.apt_households END "
                "FROM complex c WHERE c.approval_year IS NOT NULL AND c.approval_year <= ? "
                "  AND c.lawd_cd IS NOT NULL", (AS_OF_YEAR - MIN_AGE_YEARS,)):
            if cid in rows:
                rows[cid]["existing_far"] = _num(str(far)) if far is not None else rows[cid]["existing_far"]
                rows[cid]["land_share"] = _num(str(ls)) if ls is not None else NA
                continue
            put(cid, name, lawd, region_of.get(lawd[:2], "?"), NA, NA, "", None, -1, "",
                "공식 정비사업 기록 없음(선별 후보)",
                existing_far=_num(str(far)) if far is not None else NA,
                households=str(hh) if hh else NA)
            rows[cid]["stage_verification"] = "NO_OFFICIAL_RECORD"
            rows[cid]["land_share"] = _num(str(ls)) if ls is not None else NA
            rows[cid]["option_research_priority"] = "LOW"
    return sorted(rows.values(), key=lambda r: (-int(r["option_stage"]), r["region"], r["complex_name"]))


def main() -> int:
    rows = build()
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    by_stage = Counter((r["option_stage"], r["option_stage_label"]) for r in rows)
    print(f"option_stage_registry: {len(rows)}건 → {OUT.name}")
    for (s, lbl), n in sorted(by_stage.items()):
        print(f"  Stage {s} {lbl:22s} {n:5d}")
    print("  option_value 계산된 건: 0 (전부 NOT_CALCULATED — §14.1 임의값 금지)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
