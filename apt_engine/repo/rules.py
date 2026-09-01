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
                "source_name", "source_url", "last_verified", "note",
                # 018·019 — 국적 축과 적용대상. 외국인 토허를 내국인에게
                # 적용하는 사고를 막으려면 이 값들이 CSV 로 들어와야 한다.
                "rule_id", "zone_group", "buyer_scope", "nationality_scope",
                "property_scope", "parcel_scope", "legal_dong_code",
                "official_notice_no", "residential_threshold_sqm",
                "commercial_threshold_sqm", "green_threshold_sqm",
                "residence_grace_allowed", "status", "confidence")),
    "tax": ("tax_rule",
            ("tax_kind", "rule_key", "conditions_json", "bracket_min", "bracket_max",
             "rate", "progressive_deduction", "fixed_amount", "rate_formula",
             "rate_decimals", "max_amount", "base_kind", "effective_from", "effective_to",
             "source_name", "source_url", "last_verified", "status", "verification",
             "note")),
    "loan": ("loan_rule",
             ("rule_key", "rule_type", "value", "conditions_json",
              "region", "regulated_area", "home_status", "first_home_buyer",
              "price_min", "price_max", "ltv", "dsr", "dti",
              "stress_rate_bp", "max_loan_amount", "residence_required",
              "effective_from", "effective_to",
              "source_name", "source_url", "last_verified", "status", "verification",
              "note")),
    "cost": ("cost_rule",
             ("cost_kind", "rule_key", "region", "conditions_json",
              "price_min", "price_max", "rate",
              "max_amount", "fixed_amount", "vat_applicable",
              "effective_from", "effective_to",
              "source_name", "source_url", "last_verified", "status", "verification",
              "note")),
}

INT_COLUMNS = {
    "bracket_min", "bracket_max", "progressive_deduction", "fixed_amount",
    "price_min", "price_max", "stress_rate_bp", "max_loan_amount",
    "residence_duty_months", "jeonse_succession_allowed", "residence_required",
    "max_amount", "regulated_area", "first_home_buyer", "vat_applicable",
    "rate_decimals",
}
FLOAT_COLUMNS = {"rate", "ltv", "dsr", "dti", "value"}
DEFAULT_ZERO = {"bracket_min", "price_min", "progressive_deduction",
                "stress_rate_bp", "residence_required", "jeonse_succession_allowed",
                "vat_applicable"}

# NOT NULL 컬럼은 비어 있을 때 채울 값이 있어야 한다.
# status 를 비우면 '시행 중'으로 본다 — 발표·예정 정책은 반드시 명시해야 한다.
DEFAULT_TEXT = {"status": "ENACTED", "conditions_json": "{}"}


class RuleImportError(ValueError):
    pass


def _coerce(column: str, value):
    text = "" if value is None else str(value).strip()
    if text == "":
        if column in DEFAULT_ZERO:
            return 0
        return DEFAULT_TEXT.get(column)
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
            values = [_coerce(c, raw.get(c)) for c in columns]
        except RuleImportError as e:
            errors.append(f"  {i}행: {e}")
            continue
        row = dict(zip(columns, values))
        # 신뢰도를 비워 두면 확인일에서 유도한다. 확인일이 없는데 VERIFIED 로
        # 적어 두는 실수를 막기 위해, 둘이 어긋나면 거부한다.
        if "verification" in row:
            if not row["verification"]:
                row["verification"] = ("VERIFIED" if row.get("last_verified")
                                       else "NEEDS_VERIFICATION")
            elif row["verification"] == "VERIFIED" and not row.get("last_verified"):
                errors.append(f"  {i}행: verification=VERIFIED 인데 last_verified 가 "
                              f"비어 있습니다. 확인한 날짜를 적으세요")
                continue
            values = [row[c] for c in columns]
        if row.get("status") not in (None, "ENACTED", "ANNOUNCED", "PROPOSED", "EXPIRED"):
            errors.append(f"  {i}행: status 는 ENACTED/ANNOUNCED/PROPOSED/EXPIRED "
                          f"중 하나여야 합니다 — {row['status']!r}")
            continue
        prepared.append(values)
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
    """원문을 확인했다고 표시. 이걸 해야 엔진이 계산에 쓴다.

    확인일과 신뢰도는 항상 같이 움직인다 — 하나만 올리면 화면에는 '확정'인데
    출처는 미확인인 상태가 만들어진다.
    """
    table, columns = TABLES[kind]
    has_verification = "verification" in columns
    sql = f"UPDATE {table} SET last_verified = ?"
    if has_verification:
        sql += ", verification = 'VERIFIED'"
    return conn.execute(sql + " WHERE id = ?", (verified_on, rule_id)).rowcount


def list_rules(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    table, _ = TABLES[kind]
    return conn.execute(f"SELECT * FROM {table} ORDER BY effective_from DESC, id").fetchall()


def coverage(conn: sqlite3.Connection) -> dict:
    """규칙이 얼마나 채워졌나 — PHASE 3 진행률."""
    out = {}
    for kind, (table, columns) in TABLES.items():
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        verified = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE last_verified IS NOT NULL "
            f"AND trim(last_verified) != ''").fetchone()[0]
        row = {"total": total, "verified": verified}
        if "verification" in columns:
            # '계산에 쓸 수 있는가'(last_verified)와 '원문을 확인했는가'(verification)는
            # 다른 질문이다. 둘을 따로 센다.
            row["confirmed"] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE verification = 'VERIFIED'"
            ).fetchone()[0]
            row["pending"] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status != 'ENACTED'").fetchone()[0]
        out[kind] = row
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
        "source_name,source_url,last_verified,note,"
        "rule_id,zone_group,buyer_scope,nationality_scope,property_scope,parcel_scope,"
        "legal_dong_code,official_notice_no,residential_threshold_sqm,"
        "commercial_threshold_sqm,green_threshold_sqm,residence_grace_allowed,"
        "status,confidence\n"
        "# 예) 11680,대치동,서울특별시장,내국인,주거용,2026-03-01,2027-02-28,24,0,,"
        "서울시 고시 제2026-XX호,https://...,2026-08-30,,"
        "LPZ_SEL_DAECHI,,ALL_BUYERS,,아파트,,1168010600,제2026-XX호,,,,0,ENACTED,CONFIRMED\n"
        "#\n"
        "# ⚠ target_scope 와 buyer_scope 를 반드시 함께 적는다.\n"
        "#   target_scope: 내국인 / 외국인 / 전체            (기존 어휘)\n"
        "#   buyer_scope : ALL_BUYERS / FOREIGN_ONLY /       (판정에 쓰는 어휘)\n"
        "#                 CORPORATE_ONLY / SPECIFIC_BUYER_TYPE / UNKNOWN\n"
        "#\n"
        "#   **ALL_BUYERS 만 내국인 투자자의 Hard Gate 에 걸린다.**\n"
        "#   외국인 대상 지정을 ALL_BUYERS 로 적으면 내국인이 그 지역\n"
        "#   아파트를 하나도 못 사게 된다 — 가장 흔하고 가장 나쁜 실수다.\n"
        "#\n"
        "# effective_to 는 필수. 무기한이면 먼 미래 날짜를 넣고 재확인 주기를 둔다\n"
        "# jeonse_succession_allowed: 1=전세 끼고 매수 가능, 0=실거주 의무로 불가\n"
        "# residence_duty_months 를 비우면 그 구역은 NEEDS_CHECK 로 막힌다\n"
        "#   (비거주 가능이라고 판정하지 않는다)\n"
        "# residence_grace_allowed: 1=유예 확인됨, 0/빈칸=확인 안 됨\n"
        "# *_threshold_sqm: 허가 대상 면적(㎡). 주거 6 / 상업공업 15 / 녹지 20 등\n"
        "# status: ENACTED(시행중) / ANNOUNCED / PROPOSED / EXPIRED\n"),
    "tax": (
        "tax_kind,rule_key,conditions_json,bracket_min,bracket_max,rate,"
        "progressive_deduction,fixed_amount,rate_formula,rate_decimals,max_amount,base_kind,"
        "effective_from,effective_to,source_name,source_url,last_verified,"
        "status,verification,note\n"
        "# 예) 취득세,acq/1주택/6억이하,\"{\"\"house_count\"\":1}\",0,600000000,0.01,0,,,,,"
        "취득가액,2020-08-12,,지방세법 제11조,https://www.law.go.kr/...,2026-08-30,"
        "ENACTED,VERIFIED,\n"
        "# tax_kind: 취득세/취득세감면/지방교육세/농어촌특별세/재산세/종합부동산세/"
        "양도소득세/지방소득세/부가가치세/인지세\n"
        "# status: ENACTED(시행 중) / ANNOUNCED(발표·시행 전) / PROPOSED / EXPIRED\n"
        "#   → 계산에 쓰는 것은 ENACTED 뿐. 나머지는 '향후 정책 변경 가능' 안내로만 나온다\n"
        "# verification: VERIFIED / ESTIMATED / UNKNOWN / NEEDS_VERIFICATION\n"
        "#   → VERIFIED 로 적으려면 last_verified 에 확인한 날짜가 있어야 한다\n"
        "# bracket_min~max 는 과세표준 구간(원). max 를 비우면 무한\n"
        "# rate 는 0.01 또는 1% 둘 다 됨. conditions_json 연산자: _gte _lte _gt _lt _in\n"
        "# 구간 안에서 세율이 연속 변하면 rate_formula 를 쓴다 (변수는 base 하나뿐)\n"
        "#   예: 취득세 6~9억 = (base * 2 / 300000000 - 3) / 100\n"
        "# 감면분 농어촌특별세는 rule_key 에 '감면' 을 넣어야 일반분과 구분된다\n"
        "# ★ 세율은 반드시 국가법령정보센터 원문을 보고 채운다. 기억으로 적지 말 것\n"),
    "loan": (
        "rule_key,rule_type,value,conditions_json,region,regulated_area,home_status,"
        "first_home_buyer,price_min,price_max,ltv,dsr,dti,stress_rate_bp,"
        "max_loan_amount,residence_required,effective_from,effective_to,"
        "source_name,source_url,last_verified,status,verification,note\n"
        "# 예) ltv/무주택/비규제,LTV,0.70,,,0,무주택,,0,,,,,,,0,2024-09-01,,"
        "금융위원회 보도자료,https://www.fsc.go.kr/...,2026-08-30,ENACTED,VERIFIED,\n"
        "# ★ 한 행에 정책 하나. rule_type 을 반드시 적는다:\n"
        "#   LTV          value = 0.70 또는 70\n"
        "#   DSR          value = 0.40 또는 40\n"
        "#   STRESS_DSR   value = 가산금리 bp (150 = 1.5%p). 한도 계산에만 쓰인다\n"
        "#   MORTGAGE_CAP value = 대출 총액 상한(원)\n"
        "#   DTI          value = 0.60 또는 60\n"
        "# 조건 컬럼을 비우면 '무관'. 값을 적으면 반드시 맞아야 그 규칙이 잡힌다\n"
        "#   region / regulated_area(1·0) / home_status(무주택·1주택·다주택) /\n"
        "#   first_home_buyer(1·0) / price_min~max\n"
        "# 최종 한도 = min(LTV, DSR, 절대상한, 요청액). 하나라도 없으면 '확인 불가'다\n"
        "# ★ 백테스트가 이 표에 의존한다. 정책이 바뀌면 기존 행의 effective_to 를\n"
        "#   채우고 새 행을 추가한다. 덮어쓰면 과거 분석이 조용히 틀려진다\n"),
    "cost": (
        "cost_kind,rule_key,region,conditions_json,price_min,price_max,rate,"
        "max_amount,fixed_amount,vat_applicable,effective_from,effective_to,"
        "source_name,source_url,last_verified,status,verification,note\n"
        "# 예) 중개보수,brok/6억~9억,서울,,600000000,900000000,0.005,,,1,"
        "2021-10-19,,공인중개사법 시행규칙,https://...,2026-08-30,ENACTED,VERIFIED,\n"
        "# cost_kind: 중개보수 / 법무비 / 인지세 / 국민주택채권 / 등기신청수수료 /"
        " 증명서발급 / 기타\n"
        "# region 을 비우면 전국 적용. 중개보수는 시·도 조례라 지역을 반드시 적는다\n"
        "# vat_applicable=1 이면 부가가치세가 별도로 붙는다"
        " (세율은 tax 표의 부가가치세 규칙에서 온다)\n"
        "# 법무비는 대한법무사협회 보수표의 구간별 기본보수를 넣는다."
        " 정액 30만원 같은 임의값을 쓰지 않는다\n"),
}


def blanks(root: str | Path = "rules") -> dict[str, list[tuple[int, str]]]:
    """규칙 CSV 안의 '채워야 할 주석 행' 을 모은다.

    각 CSV 는 아직 값을 못 받은 규칙을 `#` 로 주석 처리한 채 자리만 만들어 둔다.
    (`<세율>` 처럼 꺾쇠로 감싼 자리표시자가 들어 있다.) 파일을 하나씩 열어보지
    않아도 "지금 뭐가 비어 있나" 를 한 번에 보려고 만든 함수다.
    """
    out: dict[str, list[tuple[int, str]]] = {}
    folder = Path(root)
    if not folder.exists():
        return out
    for path in sorted(folder.glob("*.csv")):
        found = []
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            text = line.lstrip()
            if text.startswith("#") and "<" in text and ">" in text:
                found.append((n, text.lstrip("#").strip()))
        if found:
            out[path.name] = found
    return out


def write_template(kind: str, path: str | Path) -> Path:
    if kind not in TEMPLATES:
        raise RuleImportError(f"알 수 없는 규칙 종류: {kind} (가능: {', '.join(TEMPLATES)})")
    p = Path(path)
    p.write_text(TEMPLATES[kind], encoding="utf-8")
    return p
