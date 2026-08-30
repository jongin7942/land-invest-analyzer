"""규칙 수기 입력 — 공식 API 가 없는 데이터를 사람이 넣는 경로.

세법·규제지역·토허·대출·부대비용은 기계가 읽을 수 있는 공식 출처가 없다.
그래서 CSV 로 넣고, 각 행에 **출처 URL 과 확인일**을 함께 적게 한다.
확인일(`last_verified`)이 빈 행은 엔진이 계산에 쓰지 않는다.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

TABLES = {
    "regulation": ("regulation_zone",
                   ("lawd_cd", "emd_name", "zone_type", "effective_from", "effective_to",
                    "source_name", "source_url", "last_verified", "note")),
    "permit": ("land_permit_zone",
               ("lawd_cd", "emd_name", "designator", "target_scope", "target_use",
                "effective_from", "effective_to", "residence_duty_months",
                "jeonse_succession_allowed", "resale_restriction",
                "source_name", "source_url", "last_verified", "note")),
    "tax": ("tax_rule",
            ("tax_kind", "rule_key", "conditions_json", "bracket_min", "bracket_max",
             "rate", "progressive_deduction", "fixed_amount", "rate_formula",
             "effective_from", "effective_to",
             "source_name", "source_url", "last_verified", "note")),
    "loan": ("loan_rule",
             ("rule_key", "conditions_json", "price_min", "price_max", "ltv", "dsr", "dti",
              "stress_rate_bp", "max_loan_amount", "residence_required",
              "effective_from", "effective_to",
              "source_name", "source_url", "last_verified", "note")),
    "cost": ("cost_rule",
             ("cost_kind", "rule_key", "region", "price_min", "price_max", "rate",
              "max_amount", "fixed_amount", "effective_from", "effective_to",
              "source_name", "source_url", "last_verified", "note")),
}

INT_COLUMNS = {
    "bracket_min", "bracket_max", "progressive_deduction", "fixed_amount",
    "price_min", "price_max", "stress_rate_bp", "max_loan_amount",
    "residence_duty_months", "jeonse_succession_allowed", "residence_required",
    "max_amount",
}
FLOAT_COLUMNS = {"rate", "ltv", "dsr", "dti"}
DEFAULT_ZERO = {"bracket_min", "price_min", "progressive_deduction",
                "stress_rate_bp", "residence_required", "jeonse_succession_allowed"}


class RuleImportError(ValueError):
    pass


def _coerce(column: str, value):
    text = "" if value is None else str(value).strip()
    if text == "":
        return 0 if column in DEFAULT_ZERO else None
    if column in INT_COLUMNS:
        try:
            return int(float(text.replace(",", "")))
        except ValueError as e:
            raise RuleImportError(f"{column} 은 숫자여야 합니다: {value!r}") from e
    if column in FLOAT_COLUMNS:
        try:
            return float(text.replace("%", "")) / (100 if "%" in text else 1)
        except ValueError as e:
            raise RuleImportError(f"{column} 은 숫자여야 합니다: {value!r}") from e
    if column == "conditions_json":
        try:
            json.loads(text)
        except ValueError as e:
            raise RuleImportError(f"conditions_json 이 JSON 이 아닙니다: {value!r}") from e
    return text


def import_csv(conn: sqlite3.Connection, kind: str, path: str | Path) -> dict:
    """규칙 CSV 를 읽어 넣는다. 한 줄이라도 잘못되면 전부 넣지 않는다."""
    if kind not in TABLES:
        raise RuleImportError(f"알 수 없는 규칙 종류: {kind} (가능: {', '.join(TABLES)})")
    table, columns = TABLES[kind]

    with open(path, newline="", encoding="utf-8-sig") as f:
        # 서식의 '#' 주석 줄은 데이터가 아니다.
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    raw_rows = [r for r in csv.DictReader(lines)
                if any(str(v or "").strip() for v in r.values())]
    if not raw_rows:
        return {"read": 0, "inserted": 0, "unverified": 0}

    prepared, errors = [], []
    for i, raw in enumerate(raw_rows, start=2):
        try:
            prepared.append([_coerce(c, raw.get(c)) for c in columns])
        except RuleImportError as e:
            errors.append(f"  {i}행: {e}")
    if errors:
        raise RuleImportError(f"{path} 에서 {len(errors)}개 줄을 읽지 못했습니다:\n"
                              + "\n".join(errors[:20]))

    placeholders = ",".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", prepared)

    verified_idx = columns.index("last_verified")
    unverified = sum(1 for r in prepared if not r[verified_idx])
    return {"read": len(raw_rows), "inserted": len(prepared), "unverified": unverified}


def mark_verified(conn: sqlite3.Connection, kind: str, *, rule_id: int,
                  verified_on: str) -> int:
    """원문을 확인했다고 표시. 이걸 해야 엔진이 계산에 쓴다."""
    table, _ = TABLES[kind]
    return conn.execute(f"UPDATE {table} SET last_verified = ? WHERE id = ?",
                        (verified_on, rule_id)).rowcount


def list_rules(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    table, _ = TABLES[kind]
    return conn.execute(f"SELECT * FROM {table} ORDER BY effective_from DESC, id").fetchall()


def coverage(conn: sqlite3.Connection) -> dict:
    """규칙이 얼마나 채워졌나 — PHASE 3 진행률."""
    out = {}
    for kind, (table, _) in TABLES.items():
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        verified = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE last_verified IS NOT NULL "
            f"AND trim(last_verified) != ''").fetchone()[0]
        out[kind] = {"total": total, "verified": verified}
    return out


# ── 입력 서식 ──────────────────────────────────────────────────────────
# 값을 채워 넣지 않는다. 세율을 우리가 적어 두면 그게 곧 하드코딩이고,
# 사용자가 확인 없이 그대로 쓰게 된다(요구사항 62-10).
# 컬럼과 예시 형식만 주고, 실제 값은 원문을 보고 채우게 한다.

TEMPLATES = {
    "regulation": (
        "lawd_cd,emd_name,zone_type,effective_from,effective_to,"
        "source_name,source_url,last_verified,note\n"
        "# 예) 11680,,조정대상지역,2023-01-05,,국토교통부 공고 제2023-XX호,"
        "https://www.molit.go.kr/...,2026-08-30,강남구 전역\n"
        "# zone_type: 조정대상지역 / 투기과열지구 / 투기지역\n"
        "# effective_to 를 비우면 '현재까지 유효' 로 본다\n"
        "# last_verified 를 비우면 엔진이 이 규칙으로 계산하지 않는다\n"),
    "permit": (
        "lawd_cd,emd_name,designator,target_scope,target_use,effective_from,effective_to,"
        "residence_duty_months,jeonse_succession_allowed,resale_restriction,"
        "source_name,source_url,last_verified,note\n"
        "# 예) 11680,대치동,서울특별시장,내국인,주거용,2026-03-01,2027-02-28,24,0,,"
        "서울시 고시 제2026-XX호,https://...,2026-08-30,\n"
        "# target_scope: 내국인 / 외국인 / 전체  ← 절대 섞지 말 것\n"
        "# effective_to 는 필수. 무기한이면 먼 미래 날짜를 넣고 재확인 주기를 둔다\n"
        "# jeonse_succession_allowed: 1=전세 끼고 매수 가능, 0=실거주 의무로 불가\n"),
    "tax": (
        "tax_kind,rule_key,conditions_json,bracket_min,bracket_max,rate,"
        "progressive_deduction,fixed_amount,rate_formula,effective_from,effective_to,"
        "source_name,source_url,last_verified,note\n"
        "# 예) 취득세,acq/1주택/6억이하,\"{\"\"house_count\"\":1}\",0,600000000,0.01,0,,,"
        "2020-08-12,,지방세법 제11조,https://www.law.go.kr/...,2026-08-30,\n"
        "# tax_kind: 취득세/지방교육세/농어촌특별세/재산세/종합부동산세/양도소득세/지방소득세\n"
        "# bracket_min~max 는 과세표준 구간(원). max 를 비우면 무한\n"
        "# rate 는 0.01 또는 1% 둘 다 됨. conditions_json 연산자: _gte _lte _gt _lt _in\n"
        "# ★ 세율은 반드시 국가법령정보센터 원문을 보고 채운다. 기억으로 적지 말 것\n"),
    "loan": (
        "rule_key,conditions_json,price_min,price_max,ltv,dsr,dti,stress_rate_bp,"
        "max_loan_amount,residence_required,effective_from,effective_to,"
        "source_name,source_url,last_verified,note\n"
        "# 예) ltv/무주택/비규제,\"{\"\"house_count\"\":0,\"\"regulated\"\":false}\",0,,0.70,0.40,,"
        "150,,0,2024-09-01,,금융위원회 보도자료,https://...,2026-08-30,\n"
        "# ltv/dsr 은 0.70 또는 70% 둘 다 됨. stress_rate_bp 는 스트레스 DSR 가산금리(bp)\n"),
    "cost": (
        "cost_kind,rule_key,region,price_min,price_max,rate,max_amount,fixed_amount,"
        "effective_from,effective_to,source_name,source_url,last_verified,note\n"
        "# 예) 중개보수,brok/6억~9억,서울,600000000,900000000,0.005,,,"
        "2021-10-19,,공인중개사법 시행규칙,https://...,2026-08-30,\n"
        "# cost_kind: 중개보수 / 법무비 / 인지세 / 국민주택채권 / 기타\n"
        "# region 을 비우면 전국 적용\n"),
}


def write_template(kind: str, path: str | Path) -> Path:
    if kind not in TEMPLATES:
        raise RuleImportError(f"알 수 없는 규칙 종류: {kind} (가능: {', '.join(TEMPLATES)})")
    p = Path(path)
    p.write_text(TEMPLATES[kind], encoding="utf-8")
    return p
