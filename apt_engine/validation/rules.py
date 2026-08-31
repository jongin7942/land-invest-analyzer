"""검증 계층 — 요구사항 26의 "절대 발생하면 안 되는 오류" 12가지.

상당수는 이미 **스키마가 막고 있다.** 총세대수 컬럼을 안 만들었으니 아파트와
오피스텔 세대수를 더할 수 없고, `complex_group.merge_reason` 이 NOT NULL 이라
근거 없이 단지를 합칠 수 없다. 막힌 것을 다시 검사하는 건 낭비다.

여기서는 **스키마로 못 막는 것**을 검사한다 — 값이 비었거나, 서로 어긋나거나,
수집이 빠졌거나 하는 것들. 각 규칙은 위반 메시지 목록을 돌려주고, 비어 있으면 통과다.

    python -m apt_engine.cli validate

PHASE 3(토허·세법 기준연도), PHASE 5(GTX 단계), PHASE 6(용적률 구분)에 해당하는
규칙은 그 데이터가 생길 때 이 파일에 추가한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from apt_engine import area

Check = Callable[[sqlite3.Connection], list[str]]


@dataclass(frozen=True)
class Rule:
    rule_id: str          # 요구사항 26의 번호 또는 자체 번호
    title: str
    severity: str         # ERROR = 반드시 고쳐야 함 / WARN = 확인 필요
    check: Check


_RULES: list[Rule] = []


def rule(rule_id: str, title: str, severity: str = "ERROR"):
    def deco(fn: Check) -> Check:
        _RULES.append(Rule(rule_id, title, severity, fn))
        return fn
    return deco


def all_rules() -> list[Rule]:
    return list(_RULES)


# ── 26-1 / 26-2 · 세대수 ───────────────────────────────────────────────

@rule("26-1/2", "세대수 합계 컬럼이 존재하지 않는다 (합치면 필터가 오염된다)")
def no_total_households(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(complex)")}
    bad = cols & {"total_households", "households", "total_cnt"}
    if bad:
        return [f"complex 에 세대수 합계 컬럼이 생겼습니다: {sorted(bad)}. "
                f"세대수 필터는 apt_households 만 봐야 합니다."]
    return []


@rule("26-1", "세대수가 없는 단지는 세대수 필터 결과에 들어가지 않는다", severity="WARN")
def households_unknown(conn):
    n = conn.execute(
        "SELECT COUNT(*) FROM complex WHERE apt_households IS NULL").fetchone()[0]
    if n:
        return [f"세대수 미상 단지 {n}개 — K-apt 기본정보 수집이 덜 됐습니다"
                f" (`cli collect complexes --basis`). 필터에서 조용히 빠집니다."]
    return []


# ── 26-3 · 단지 병합 ───────────────────────────────────────────────────

@rule("26-3", "근거 없이 묶인 단지 그룹이 없다")
def merge_reason_present(conn):
    rows = conn.execute(
        "SELECT id, name FROM complex_group "
        "WHERE merge_reason IS NULL OR trim(merge_reason) = ''").fetchall()
    return [f"단지 그룹 #{r['id']} '{r['name']}' 에 병합 근거가 없습니다" for r in rows]


# ── 26-4 · 면적 ────────────────────────────────────────────────────────

@rule("26-4", "모든 거래의 area_band 가 전용면적과 일치한다")
def area_band_consistent(conn):
    problems = []
    for table in ("trade", "jeonse_contract"):
        rows = conn.execute(
            f"SELECT id, exclusive_area_m2, area_band FROM {table}").fetchall()
        for r in rows:
            try:
                expected = area.band_of(r["exclusive_area_m2"])
            except area.AreaBandError as e:
                problems.append(f"{table}#{r['id']}: {e}")
                continue
            if expected != r["area_band"]:
                problems.append(
                    f"{table}#{r['id']}: 전용 {r['exclusive_area_m2']}㎡ 는 "
                    f"'{expected}' 밴드인데 '{r['area_band']}' 로 저장됨")
            if len(problems) >= 20:
                return problems + ["… (이하 생략)"]
    return problems


@rule("26-4", "국민평형 밴드(84)는 80~85㎡ 만 포함한다")
def kookmin_band_range(conn):
    lo, hi = area.range_of("84")
    if (lo, hi) != (80.0, 85.0):
        return [f"'84' 밴드 범위가 {lo}~{hi} 입니다. 요구사항은 80~85㎡ 입니다."]
    n = conn.execute(
        "SELECT COUNT(*) FROM trade WHERE area_band='84' "
        "AND (exclusive_area_m2 < 80.0 OR exclusive_area_m2 >= 85.0)").fetchone()[0]
    return [f"'84' 밴드에 80~85㎡ 밖의 거래가 {n}건 섞여 있습니다"] if n else []


# ── 26-5 / 26-6 · 거래 성격 ────────────────────────────────────────────

@rule("26-6", "거래유형(중개/직거래)이 수집돼 있다 — 없으면 직거래를 걸러낼 수 없다")
def deal_type_present(conn):
    total = conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0]
    if not total:
        return []
    missing = conn.execute(
        "SELECT COUNT(*) FROM trade WHERE deal_type IS NULL OR trim(deal_type)=''"
    ).fetchone()[0]
    if missing == total:
        return ["모든 매매 거래에 거래유형이 비어 있습니다. 상세 데이터셋"
                "(RTMSDataSvcAptTradeDev)이 아니거나 필드명이 바뀌었습니다 — "
                "`cli probe trade` 로 확인하세요."]
    if missing:
        return [f"거래유형이 비어 있는 매매 {missing}건 / 전체 {total}건 "
                f"({missing / total * 100:.1f}%)"]
    return []


@rule("26-5", "해제(취소) 여부가 수집돼 있다", severity="WARN")
def cancel_flag_sane(conn):
    total = conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0]
    if total < 100:
        return []
    cancelled = conn.execute("SELECT COUNT(*) FROM trade WHERE cancel_yn=1").fetchone()[0]
    if cancelled == 0:
        return ["해제 거래가 한 건도 없습니다. 실제로는 소수라도 존재하는 게 정상이라, "
                "해제여부 필드를 못 읽고 있을 가능성이 있습니다 — `cli probe trade` 확인."]
    return []


# ── 매칭 품질 ──────────────────────────────────────────────────────────

UNMATCHED_LIMIT_PCT = 5.0


@rule("M-1", f"미매칭 거래 비율이 {UNMATCHED_LIMIT_PCT}% 미만이다")
def match_rate(conn):
    out = []
    for table, label in (("trade", "매매"), ("jeonse_contract", "전월세")):
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not total:
            continue
        un = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE complex_id IS NULL").fetchone()[0]
        pct = un / total * 100
        if pct >= UNMATCHED_LIMIT_PCT:
            out.append(f"{label} 미매칭 {un}/{total}건 ({pct:.1f}%) — "
                       f"`cli report unmatched` 로 상위 이름을 확인하세요")
    return out


@rule("M-2", "WEAK 매칭은 검증 대상이다", severity="WARN")
def weak_matches(conn):
    out = []
    for table, label in (("trade", "매매"), ("jeonse_contract", "전월세")):
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE match_confidence='WEAK'").fetchone()[0]
        if n:
            out.append(f"{label} WEAK 매칭 {n}건 — 유사도만으로 붙였습니다. "
                       f"대표가격에 쓰기 전 표본 확인 권장")
    return out


# ── 무결성 ─────────────────────────────────────────────────────────────

@rule("D-1", "금액이 원 단위 양수로 저장돼 있다")
def amounts_sane(conn):
    out = []
    # 만원 단위로 잘못 저장하면 아파트 가격이 10만~수십만 원대로 찍힌다.
    n = conn.execute(
        "SELECT COUNT(*) FROM trade WHERE deal_amount < 10000000").fetchone()[0]
    if n:
        out.append(f"매매가가 1,000만원 미만인 거래 {n}건 — 만원/원 단위 혼동 의심")
    # SQLite 는 동적 타입이라 INTEGER 컬럼에도 실수가 들어갈 수 있다.
    n = conn.execute(
        "SELECT COUNT(*) FROM trade WHERE typeof(deal_amount) != 'integer'").fetchone()[0]
    if n:
        out.append(f"매매가가 정수가 아닌 거래 {n}건 — 원 단위 int 규약 위반")
    return out


@rule("D-2", "거래일이 YYYYMMDD 형식이다")
def ymd_format(conn):
    out = []
    for table, col in (("trade", "deal_ymd"), ("jeonse_contract", "contract_ymd")):
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE length({col}) != 8 OR {col} GLOB '*[^0-9]*'"
        ).fetchone()[0]
        if n:
            out.append(f"{table}.{col} 형식이 잘못된 행 {n}건")
    return out


@rule("D-3", "외래키 위반이 없다")
def foreign_keys(conn):
    rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    return [f"외래키 위반: {r[0]} rowid={r[1]} → {r[2]}" for r in rows[:20]]


# ── 26-5 · 대표가격 (PHASE 2) ──────────────────────────────────────────

@rule("26-5", "단일 신고가를 대표가격으로 쓰지 않는다")
def not_single_high(conn):
    """표본이 3건 이상인데 대표가격이 최고가와 같으면, 사실상 신고가를 시세로 쓴 것이다."""
    if not _has_table(conn, "price_snapshot"):
        return []
    rows = conn.execute(
        "SELECT id, complex_id, area_band, as_of_ym, sample_n, "
        "representative_price, price_max FROM price_snapshot "
        "WHERE sample_n >= 3 AND representative_price = price_max").fetchall()
    return [f"price_snapshot#{r['id']} (단지 {r['complex_id']}·{r['area_band']}·{r['as_of_ym']}): "
            f"표본 {r['sample_n']}건인데 대표가격이 최고가와 같습니다" for r in rows[:20]]


@rule("2", "대표가격에 신뢰도가 붙어 있다")
def snapshot_confidence(conn):
    if not _has_table(conn, "price_snapshot"):
        return []
    n = conn.execute(
        "SELECT COUNT(*) FROM price_snapshot WHERE confidence IS NULL").fetchone()[0]
    return [f"신뢰도가 없는 대표가격 {n}건"] if n else []


@rule("2", "표본이 적은 대표가격은 LOW 로 표시된다", severity="WARN")
def low_confidence_snapshots(conn):
    if not _has_table(conn, "price_snapshot"):
        return []
    n = conn.execute(
        "SELECT COUNT(*) FROM price_snapshot WHERE confidence='LOW'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM price_snapshot").fetchone()[0]
    if not total or not n:
        return []
    return [f"정상거래 1~2건으로 산출된 대표가격 {n}/{total}건 ({n/total*100:.0f}%) — "
            f"단독 근거로 쓰지 말 것"]


@rule("25", "모든 대표가격에 계산근거(calc_trace)가 저장돼 있다")
def snapshot_has_trace(conn):
    if not _has_table(conn, "price_snapshot"):
        return []
    out = []
    for table in ("price_snapshot", "jeonse_snapshot"):
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE calc_trace IS NULL OR trim(calc_trace) = '' "
            f"OR engine_version IS NULL").fetchone()[0]
        if n:
            out.append(f"{table} 에 계산근거 없는 행 {n}건 — 근거 없는 숫자는 표시할 수 없다")
    return out


@rule("28", "전세가율이 상식 범위 안에 있다", severity="WARN")
def jeonse_ratio_sane(conn):
    if not _has_table(conn, "jeonse_snapshot"):
        return []
    rows = conn.execute(
        "SELECT id, complex_id, area_band, jeonse_ratio FROM jeonse_snapshot "
        "WHERE jeonse_ratio IS NOT NULL AND (jeonse_ratio < 0.1 OR jeonse_ratio > 1.2)"
    ).fetchall()
    return [f"jeonse_snapshot#{r['id']} (단지 {r['complex_id']}·{r['area_band']}): "
            f"전세가율 {r['jeonse_ratio']*100:.0f}% — 매매/전세 표본 확인 필요"
            for r in rows[:20]]


# ── PHASE 6 · 재건축 사업성 ────────────────────────────────────────────

@rule("62-7", "법정 최대 용적률이 정비계획 용적률로 둔갑하지 않았다")
def far_kind_not_confused(conn):
    if not _has_table(conn, "redevelopment_project"):
        return []
    out = []
    rows = conn.execute(
        "SELECT p.complex_id, p.planned_far, p.stage, f.max_far, f.kind "
        "  FROM redevelopment_project p "
        "  JOIN complex c ON c.id = p.complex_id "
        "  JOIN far_standard f ON f.zoning = c.zoning "
        " WHERE p.planned_far IS NOT NULL AND f.kind = '법정상한' "
        "   AND abs(p.planned_far - f.max_far) < 0.01").fetchall()
    for r in rows:
        out.append(f"단지 {r['complex_id']}: 정비계획 용적률 {r['planned_far']:g}% 가 "
                   f"법정상한과 같습니다 — 상한을 고시값으로 적은 것은 아닌지 확인하세요")
    return out


@rule("62-7", "정비계획 용적률은 구역지정 이후 단계에만 있다")
def planned_far_needs_stage(conn):
    if not _has_table(conn, "redevelopment_project"):
        return []
    rows = conn.execute(
        "SELECT complex_id, stage, planned_far FROM redevelopment_project "
        " WHERE planned_far IS NOT NULL "
        "   AND stage IN ('미지정','예비안전진단','정밀안전진단')").fetchall()
    return [f"단지 {r['complex_id']}: '{r['stage']}' 단계인데 정비계획 용적률 "
            f"{r['planned_far']:g}% 가 있습니다" for r in rows]


@rule("18/62-5", "재건축 사업성은 시나리오 등급으로만 저장돼 있다")
def scenario_grade_only(conn):
    if not _has_table(conn, "redevelopment_scenario"):
        return []
    n = conn.execute(
        "SELECT COUNT(*) FROM redevelopment_scenario "
        " WHERE data_grade != 'SCENARIO' OR calc_trace IS NULL "
        "    OR trim(calc_trace) = ''").fetchone()[0]
    return ([f"redevelopment_scenario 에 확정 등급이거나 근거 없는 행 {n}건 — "
             f"추가분담금을 확정 금액처럼 저장할 수 없습니다"] if n else [])


@rule("18", "한 단지의 분담금이 구간(3개 시나리오)으로 저장돼 있다", severity="WARN")
def scenario_is_a_band(conn):
    if not _has_table(conn, "redevelopment_scenario"):
        return []
    rows = conn.execute(
        "SELECT complex_id, area_band, as_of, COUNT(*) AS n "
        "  FROM redevelopment_scenario GROUP BY 1,2,3 HAVING n < 3").fetchall()
    return [f"단지 {r['complex_id']}({r['area_band']}·{r['as_of']}): 시나리오가 "
            f"{r['n']}개뿐입니다 — 분담금은 하나의 숫자로 말하지 않습니다" for r in rows[:20]]


@rule("30", "대지면적에 출처가 남아 있다", severity="WARN")
def land_area_has_source(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(complex)")}
    if "land_area_source" not in cols:
        return []
    n = conn.execute(
        "SELECT COUNT(*) FROM complex WHERE land_area_m2 IS NOT NULL "
        " AND (land_area_source IS NULL OR trim(land_area_source) = '')").fetchone()[0]
    return ([f"출처 없는 대지면적 {n}건 — 대지지분은 사업성의 뿌리라 검증이 필요합니다"]
            if n else [])


@rule("17", "정비사업 단계에는 그 단계가 된 날이 함께 있다")
def stage_has_date(conn):
    if not _has_table(conn, "redevelopment_project"):
        return []
    n = conn.execute(
        "SELECT COUNT(*) FROM redevelopment_project "
        " WHERE stage != '미지정' AND (stage_date IS NULL OR trim(stage_date) = '')"
    ).fetchone()[0]
    return ([f"단계일자 없는 정비사업 {n}건"] if n else [])


# ── PHASE 7 · 현금흐름 ─────────────────────────────────────────────────

@rule("30/31", "수익률은 시나리오 등급으로만 저장돼 있다")
def cashflow_grade_only(conn):
    if not _has_table(conn, "cashflow_snapshot"):
        return []
    n = conn.execute(
        "SELECT COUNT(*) FROM cashflow_snapshot "
        " WHERE data_grade != 'SCENARIO' OR calc_trace IS NULL "
        "    OR trim(calc_trace) = ''").fetchone()[0]
    return ([f"cashflow_snapshot 에 확정 등급이거나 근거 없는 행 {n}건 — "
             f"세후 IRR 을 확정 수익률처럼 저장할 수 없습니다"] if n else [])


@rule("30", "Peak Equity 가 Initial Equity 보다 작지 않다")
def peak_not_below_initial(conn):
    if not _has_table(conn, "cashflow_snapshot"):
        return []
    rows = conn.execute(
        "SELECT id, complex_id, scenario_key, initial_equity, peak_equity "
        "  FROM cashflow_snapshot "
        " WHERE initial_equity IS NOT NULL AND peak_equity IS NOT NULL "
        "   AND peak_equity < initial_equity").fetchall()
    return [f"cashflow_snapshot#{r['id']} ({r['scenario_key']}): "
            f"Peak {r['peak_equity']:,} < Initial {r['initial_equity']:,} — "
            f"보유 중 묶인 돈이 처음보다 적을 수는 없습니다" for r in rows[:20]]


@rule("31", "한 단지의 수익률이 구간(여러 시나리오)으로 저장돼 있다", severity="WARN")
def cashflow_is_a_band(conn):
    if not _has_table(conn, "cashflow_snapshot"):
        return []
    rows = conn.execute(
        "SELECT complex_id, as_of, holding_years, COUNT(*) AS n "
        "  FROM cashflow_snapshot GROUP BY 1,2,3 HAVING n < 2").fetchall()
    return [f"단지 {r['complex_id']}({r['as_of']}·{r['holding_years']}년): "
            f"시나리오가 {r['n']}개뿐입니다 — 수익률은 하나의 숫자로 말하지 않습니다"
            for r in rows[:20]]


def _has_table(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# ── 실행 ───────────────────────────────────────────────────────────────

def run_all(conn: sqlite3.Connection) -> list[tuple[Rule, list[str]]]:
    """(규칙, 위반메시지들) 목록. 위반이 없는 규칙도 빈 리스트로 포함한다."""
    return [(r, r.check(conn)) for r in _RULES]


def summarize(results: list[tuple[Rule, list[str]]]) -> dict:
    errors = sum(1 for r, v in results if v and r.severity == "ERROR")
    warns = sum(1 for r, v in results if v and r.severity == "WARN")
    return {"total": len(results), "passed": sum(1 for _, v in results if not v),
            "errors": errors, "warnings": warns}
