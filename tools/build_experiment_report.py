"""세 실험 + 사회적 승강장 H1 결과 산출물 생성 (2026-09-07).

reports/three_experiments_{60,36}.json, social_escalator_h1_{60,36}.json →
  reports/SOCIAL_ESCALATOR_COMPARISON.csv, SOCIAL_ESCALATOR_PREDICTIONS.csv,
  reports/SOCIAL_ESCALATOR_FEATURE_DICTIONARY.csv, reports/SOCIAL_ESCALATOR_FAILURE_CASES.md
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
R = ROOT / "reports"


def load(name):
    p = R / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


FEATURES = [
    ("hedonic_resid", "헤도닉 정상가격 대비 할인율", "log", "자체 실거래 패널 + 통제변수", "진입월 횡단면 ridge(면적·연식·연식²·세대·급지·1급지거리·중심거리·학원·직장·역거리·용적률) 잔차", "C 모형추정", "부분중복: rel_gu·own_pct 와 목적 같음, 계산식 다름", "실험2"),
    ("subst_n0/n10/n20", "우월 대체재 수(가격 100/110/120% 이하)", "log1p 개수", "자체 실거래 패널", "같은 면적대·15km·1급지거리≤·급지≤·연식≤ 인 단지 중 가격 조건 충족 수", "C 모형추정(잠재 재고)", "신규", "실험3"),
    ("subst_share20", "대체재 비중", "비율", "자체 실거래 패널", "subst_n20 원수 ÷ 같은 면적대 후보 수", "C", "신규", "실험3"),
    ("emp_g3km/emp_g10km", "통근권 고용 연증가율(수도권 대비)", "log/년", "국민연금 사업장 가입자(법정동)", "(log 최신−log 직전 스냅샷)/연수 − 수도권 동일값. 스냅샷 연도 ≤ T−1", "A 관측(집계)", "jobs_emd·jobs_3km 는 수준, 이건 변화 — 신규", "실험1"),
    ("inc_g1/inc_g3", "시군구 1인당 총급여 성장(수도권 중앙 대비)", "log", "국세청 근로소득 연말정산(KOSIS)", "T−2 기준 1·3년 log 변화 − 수도권 중앙", "A 관측(집계)", "§27.2 에서 유사 변수 검증(전체표본) — 이번엔 자료표본 재평가", "실험1"),
    ("od_total_in", "도착지 총유입(예산 무관)", "log1p 명", "KOSIS DT_1B26003_A02", "도착 시군구로의 전체 전입자 합", "B 집계 OD", "DUPLICATE — gu_netmig 과 같은 정보(대조용)", "H1-B1"),
    ("grav_expected", "규모·거리 기대 이동량", "log1p 명", "위 + 세대재고·거리", "log F ~ log 출발전출 + log 도착재고 + log 거리 적합값 합", "B×C", "신규(대조용)", "H1-B1"),
    ("bpc_flow", "예산 적합 유입 합(선택 집중량)", "log1p 명", "위 OD + 출발지 ㎡단가", "Σ 유입 × [출발지 ㎡단가 ≥ 0.8× 후보 ㎡단가]", "B×C 혼합", "신규 관계", "H1-B2"),
    ("bpc_excess", "기대 대비 초과 유입(예산 적합 출발지 평균)", "log", "위", "mean(log 실제 − log 기대) over 적합 출발지", "B×C", "신규 관계", "H1-B2"),
    ("eff_origins", "출발지 다양성(유효 출발지 수)", "개수", "위", "exp(−Σ a log a), a=적합 유입 비중", "B×C", "신규 관계", "H1-B3(미실행)"),
    ("top_origin_share", "최대 출발지 의존도", "비율", "위", "max(a)", "B×C", "신규 관계", "H1-B3(미실행)"),
    ("retention10", "가격 +10% 시 선택 유지", "비율", "위", "bpc_flow(가격×1.1) ÷ bpc_flow(현재가)", "C", "REPRESENTATION_CHANGE_ONLY", "H1-B4(미실행)"),
]


def main() -> int:
    with (R / "SOCIAL_ESCALATOR_FEATURE_DICTIONARY.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["field", "정의", "단위", "원자료", "계산식", "측정등급", "기존변수 중복 여부", "사용 실험"]); w.writerows(FEATURES)

    rows = []
    for tag, fn in (("5년", "three_experiments_60.json"), ("3년(미사용 2022·2023)", "three_experiments_36.json")):
        d = load(fn)
        if not d: continue
        s = d["summary"]
        rows.append([tag, "A 기준·전체표본", s["A_baseline_full"].get("n_years"), s["A_baseline_full"]["hit"], s["A_baseline_full"]["excess"], s["A_baseline_full"]["tw_mult"]])
        for ex in ("exp1", "exp2", "exp3"):
            for k, v in s.get(ex, {}).items():
                rows.append([tag, f"{ex} {k}", v.get("n_years"), v.get("hit"), v.get("excess"), v.get("tw_mult")])
    for tag, fn in (("5년", "social_escalator_h1_60.json"), ("3년(미사용 2022·2023)", "social_escalator_h1_36.json")):
        d = load(fn)
        if not d: continue
        for k, v in d.get("summary", {}).items():
            rows.append([tag, f"H1 {k}", v.get("n_years"), v.get("hit"), v.get("excess"), v.get("tw_mult")])
    with (R / "SOCIAL_ESCALATOR_COMPARISON.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["지평", "모델", "연도수", "적중(TOP10중 상위10%)", "평균초과수익(log)", "순자산배율(자본3억,세금제외)"]); w.writerows(rows)

    # 예측·실제: TOP10 선택 이력
    with get_conn() as conn:
        names = {int(r["id"]): r["name"] for r in conn.execute("SELECT id, name FROM complex")}
    pred = []
    for tag, fn in (("5년", "three_experiments_60.json"), ("3년", "three_experiments_36.json"), ("5년", "social_escalator_h1_60.json"), ("3년", "social_escalator_h1_36.json")):
        d = load(fn)
        if not d: continue
        for key, val in d.items():
            if not isinstance(val, dict): continue
            block = val if all(str(k).isdigit() for k in val) else {kk: vv for kk, vv in val.items() if isinstance(vv, dict) and all(str(k2).isdigit() for k2 in vv)}
            def emit(model, yrs):
                for T, m in yrs.items():
                    if not isinstance(m, dict) or "picks" not in m: continue
                    for cid, band in m["picks"]:
                        pred.append([tag, model, T, cid, names.get(cid, ""), band, m.get("hit"), m.get("excess"), m.get("tw_mult")])
            if all(str(k).isdigit() for k in val):
                emit(key, val)
            else:
                for k2, v2 in val.items():
                    if isinstance(v2, dict) and all(str(k3).isdigit() for k3 in v2):
                        emit(f"{key}.{k2}", v2)
    with (R / "SOCIAL_ESCALATOR_PREDICTIONS.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["지평", "모델", "진입연도", "complex_id", "단지명", "면적", "그해 TOP10 적중률", "그해 평균초과수익", "그해 순자산배율"]); w.writerows(pred)
    print(f"COMPARISON {len(rows)}행 · PREDICTIONS {len(pred)}행 · FEATURE_DICTIONARY {len(FEATURES)}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
