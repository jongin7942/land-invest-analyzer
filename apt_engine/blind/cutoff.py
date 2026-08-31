"""as-of 컷오프 — 그 시점에 몰랐던 데이터를 읽지 못하게 한다 (§18·§55·§69).

백테스트에서 가장 흔하고 가장 치명적인 반칙이 look-ahead 다.
2023-01-01 시점 모델이 2024년에 확정된 GTX 노선을 알고 있으면, 그 백테스트 결과는
전부 거짓이고 실전에서는 재현되지 않는다.

"조심해서 WHERE 절을 잘 쓰자" 로는 못 막는다. 사람은 반드시 한 번은 빠뜨린다.
그래서 **컷오프를 통과하지 않은 조회는 예외를 던진다.**

    with cutoff.guard(conn, AsOf("2023-01-01")) as guarded:
        guarded.execute("SELECT * FROM trade WHERE deal_ymd <= ?", (...))   # OK
        guarded.execute("SELECT * FROM trade")                              # LookAheadError

날짜 컬럼이 있는 테이블 목록(`DATED_TABLES`)은 이 파일에 명시돼 있고,
새 테이블을 만들면서 여기 등록하지 않으면 테스트가 실패한다
(tests/test_blind.py 가 스키마와 대조한다).
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from apt_engine import rules


class LookAheadError(RuntimeError):
    """컷오프 이후 데이터에 손을 댔다. 이 백테스트는 무효다."""


# 테이블 → 그 행이 '언제 알려진 사실인가' 를 담은 컬럼.
#
# 두 종류를 구분한다.
#   사실 발생일   deal_ymd, opened_ym, stage_date …  그 일이 실제로 일어난 날
#   인지 가능일   last_verified, retrieved_at …      우리가 알 수 있게 된 날
#
# 백테스트에서 필요한 건 **인지 가능일**이지만, 공공데이터는 대개 발생일만 준다.
# 그래서 발생일을 쓰되 신고 지연(실거래는 계약 후 30일 내 신고)을 호출부가
# 감안하도록 `reporting_lag_days` 를 둔다.
DATED_TABLES: dict[str, tuple[str, ...]] = {
    "trade": ("deal_ymd",),
    "jeonse_contract": ("deal_ymd",),
    "price_snapshot": ("as_of_ym",),
    "jeonse_snapshot": ("as_of_ym",),
    "listing": ("first_seen_at",),
    "listing_snapshot": ("snapshot_date",),
    "market_pressure": ("as_of_date",),
    "field_note": ("observed_at",),
    "regulation_zone": ("effective_from",),
    "land_permit_zone": ("effective_from",),
    "tax_rule": ("effective_from",),
    "loan_rule": ("effective_from",),
    "cost_rule": ("effective_from",),
    "transit_station": ("status_date",),
    # 공급은 '언제 알았나'(announced_ym)로 컷오프한다. move_in_ym 은 '언제 들어오나'라
    # 미래여도 정상이다 — 그걸로 자르면 향후 공급을 아예 못 보게 된다.
    "supply_plan": ("announced_ym",),
    "future_catalyst": ("as_of",),
    "redevelopment_project": ("stage_date",),
    "redevelopment_scenario": ("as_of",),
    "price_ratio_history": ("as_of_ym",),
    "ratio_norm": ("as_of_ym",),
    "cashflow_snapshot": ("as_of",),
    "ranking_run": ("as_of",),
    # Phase 2 — 값마다 시점이 붙는 속성. as_of 가 없는 행은 조회에서 제외된다
    # (언제 알았는지 모르는 값을 과거 모델에 넣으면 그게 look-ahead 다).
    "complex_attribute": ("as_of",),
    "complex_job_access": ("as_of",),
    # Phase 4 — 호재 원장. 시점별 상태를 쌓고, 그 시점 행만 읽는다(§18)
    "catalyst_state": ("as_of",),
    "catalyst_exposure": ("as_of",),
}

# 컷오프와 무관한 테이블(시점 개념이 없는 마스터·참조 데이터).
# 여기 없고 DATED_TABLES 에도 없는 테이블을 조회하면 테스트가 잡는다.
TIMELESS_TABLES = frozenset({
    "_migration", "region", "complex", "complex_block", "complex_group",
    "complex_group_member", "unit_type", "data_source", "engine_version",
    "collection_log", "ladder_axis", "ladder_node", "benchmark_relation",
    "transit_project", "station_distance", "transit_analogue",
    "far_standard", "stage_duration_ref", "construction_cost_ref",
    "redev_candidate", "user_profile", "watchlist", "source_conflict",
    "source_tier", "ranking_entry", "investment_lesson",
    # Phase 2 — 시점 개념이 없는 마스터·큐레이션 데이터
    "complex_alias", "job_center", "life_zone", "life_zone_adjacency",
    "catalyst",
})

# 실거래는 계약 후 신고까지 시간이 걸린다. 그 시점에 **실제로 볼 수 있었던** 것만
# 쓰려면 컷오프를 그만큼 당겨야 한다. 이 값은 관측이 아니라 제도(신고기한)다.
REPORTING_LAG_DAYS = 30

_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


@dataclass(frozen=True)
class AsOf:
    """데이터 컷오프. 이 날짜 **이후**에 알려진 것은 존재하지 않는 것으로 본다."""
    day: str
    reporting_lag_days: int = REPORTING_LAG_DAYS

    def __post_init__(self):
        object.__setattr__(self, "day", rules.as_ymd(self.day))

    @property
    def ymd(self) -> str:
        """YYYYMMDD — trade.deal_ymd 형식."""
        return self.day.replace("-", "")

    @property
    def ym(self) -> str:
        """YYYYMM — 스냅샷 기준월 형식."""
        return self.day[:4] + self.day[5:7]

    @property
    def observable(self) -> "AsOf":
        """신고 지연을 반영해 당긴 컷오프.

        2023-01-01 에 실제로 볼 수 있었던 실거래는 대략 2022-12-01 이전 계약분이다.
        이걸 무시하면 "그날 아직 신고되지 않은 거래" 를 쓰게 된다.
        """
        from datetime import datetime, timedelta
        shifted = datetime.strptime(self.day, "%Y-%m-%d") - timedelta(
            days=self.reporting_lag_days)
        return AsOf(shifted.strftime("%Y-%m-%d"), 0)

    def clause(self, table: str, *, alias: str | None = None) -> str:
        """그 테이블에 붙일 WHERE 조각. 파라미터는 컬럼 개수만큼 넘긴다."""
        columns = DATED_TABLES.get(table)
        if not columns:
            return "1=1"
        prefix = f"{alias}." if alias else ""
        return " AND ".join(f"({prefix}{c} IS NULL OR {prefix}{c} <= ?)"
                            for c in columns)

    def params(self, table: str) -> tuple:
        columns = DATED_TABLES.get(table, ())
        out = []
        for c in columns:
            if c.endswith("_ymd"):
                out.append(self.ymd)
            elif c.endswith("_ym"):
                out.append(self.ym)
            else:
                out.append(self.day)
        return tuple(out)


def tables_in(sql: str) -> set[str]:
    """SQL 에서 읽는 테이블 이름. FROM/JOIN 뒤의 식별자만 본다."""
    return {m.group(1).lower() for m in _TABLE_RE.finditer(sql)}


class GuardedConnection:
    """컷오프를 지키는지 확인하는 커넥션 래퍼.

    날짜 컬럼이 있는 테이블을 읽는데 그 컬럼에 대한 비교가 SQL 안에 없으면
    거부한다. 완벽한 SQL 파서는 아니지만, **실수로 빠뜨린 WHERE 절**이라는
    현실의 사고 유형을 잡기에는 충분하다. 우회하려면 명시적으로
    `raw()` 를 써야 하고, 그건 코드에 흔적이 남는다.
    """

    def __init__(self, conn: sqlite3.Connection, as_of: AsOf):
        self._conn = conn
        self.as_of = as_of
        self.checked = 0

    def execute(self, sql: str, params=()):
        self._check(sql)
        self.checked += 1
        return self._conn.execute(sql, params)

    def raw(self, sql: str, params=()):
        """컷오프 검사를 건너뛴다. 정말 필요한 곳에서만 쓰고 이유를 주석으로 남긴다."""
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def _check(self, sql: str) -> None:
        for table in tables_in(sql):
            columns = DATED_TABLES.get(table)
            if not columns:
                if table not in TIMELESS_TABLES:
                    raise LookAheadError(
                        f"'{table}' 이 시점 분류에 등록돼 있지 않습니다. "
                        f"blind/cutoff.py 의 DATED_TABLES 또는 TIMELESS_TABLES 에 "
                        f"추가하세요 — 등록하지 않은 테이블은 반칙 여부를 알 수 없습니다.")
                continue
            if not any(c in sql for c in columns):
                raise LookAheadError(
                    f"'{table}' 을(를) 컷오프 없이 조회했습니다. "
                    f"{' 또는 '.join(columns)} 를 {self.as_of.day} 이하로 제한하세요. "
                    f"제한 없이 읽으면 그 시점에 몰랐던 데이터가 모델에 들어갑니다.")


@contextmanager
def guard(conn: sqlite3.Connection, as_of: AsOf):
    """백테스트 구간에서 컷오프를 강제한다."""
    yield GuardedConnection(conn, as_of)
