"""Winner / Loser Control Pair (신규 지시서 §27·§28·§29·§31·§41).

> 모델 과적합 검사용으로 Winner 만 넣지 않는다.
> 비슷한 Entry Price / Capital Cohort 에서 이후 성과가 크게 달랐던 Pair 를
> 지속적으로 구축한다.

Winner 만 모아 놓고 "우리 모델이 이 열 개를 다 찾았다" 고 하면 아무 의미가 없다.
같은 가격대에서 **안 오른 것**도 같이 넣어야, 모델이 둘을 구분하는지 볼 수 있다.

**이 표의 단지명은 Feature 가 아니다.** §41·§49-2 가 명시적으로 금지했다:

> 이 목록에 있다는 이유로 점수를 올리거나 내리지 말 것.
> 모델 Regression Test 에만 사용한다.

스키마의 `purpose` 는 'REGRESSION' 만 허용한다. 다른 값을 넣으면 INSERT 가
실패한다 — 이 표가 스코어링 경로로 새어 들어가는 것을 구조로 막는다.
그리고 `tests/test_delta.py` 가 결정 경로 코드에서 이 이름들이 문자열로
등장하지 않는지 검사한다.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# ⚠ **단지명은 이 파일에 없다.** `rules/control_pairs.csv` 와
# `rules/research_set.csv` 에서 읽는다.
#
# 왜 코드가 아니라 데이터인가: §73·§41 이 "특정 단지에 맞추지 마라" 고 했고,
# `tests/test_blind.py` 가 엔진 코드에 단지명 문자열이 있으면 실패시킨다.
# 이름이 코드에 박히면 언젠가 그 이름을 참조하는 분기가 생긴다 — 데이터로
# 두면 그럴 수가 없다. 종인님이 CSV 만 고쳐서 연구셋을 바꿀 수도 있다.
CONTROL_PAIRS_CSV = "rules/control_pairs.csv"
RESEARCH_SET_CSV = "rules/research_set.csv"

RESEARCH = "RESEARCH"
CONTROL = "CONTROL"
TOO_LATE = "TOO_LATE"
REVERSE_2021 = "REVERSE_2021"
KINDS = (RESEARCH, CONTROL, TOO_LATE, REVERSE_2021)

USAGE_NOTE = (
    "이 목록은 Regression/Research 전용입니다(§41·§49-2). 여기 있다는 이유로 "
    "Alpha 를 올리거나 내리지 않습니다. 2021 검사는 Reverse Sanity Test 이지 "
    "True Blind Test 가 아닙니다(§28)")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_research_set(kind: str | None = None,
                      path: str | Path | None = None) -> list[tuple[str, str]]:
    """연구 후보 목록. 없으면 빈 목록 — 만들어내지 않는다.

    반환: [(kind, label), ...]
    """
    target = Path(path) if path else _repo_root() / RESEARCH_SET_CSV
    if not target.exists():
        return []
    out: list[tuple[str, str]] = []
    with target.open(encoding="utf-8") as fh:
        rows = csv.DictReader(_strip_comments(fh))
        for row in rows:
            k = (row.get("kind") or "").strip()
            label = (row.get("label") or "").strip()
            if not k or not label:
                continue
            if kind is not None and k != kind:
                continue
            out.append((k, label))
    return out


def load_pairs_csv(path: str | Path | None = None) -> list[dict]:
    target = Path(path) if path else _repo_root() / CONTROL_PAIRS_CSV
    if not target.exists():
        return []
    with target.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(_strip_comments(fh))
                if (r.get("pair_key") or "").strip()]


def _strip_comments(lines):
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        yield line


@dataclass(frozen=True)
class Pair:
    pair_key: str
    as_of: str
    winner_label: str
    loser_label: str
    area_band: str
    hypothesis: str
    winner_id: int | None = None
    loser_id: int | None = None
    entry_price_gap: float | None = None
    outcome_gap: float | None = None

    @property
    def resolved(self) -> bool:
        return self.winner_id is not None and self.loser_id is not None

    @property
    def label(self) -> str:
        tail = "" if self.resolved else "  (아직 단지 매칭 안 됨)"
        return (f"{self.as_of} {self.winner_label} vs {self.loser_label} "
                f"[{self.area_band}]{tail}")


def seed(conn: sqlite3.Connection, path: str | Path | None = None) -> int:
    """CSV 의 Control Pair 를 넣는다. 이미 있으면 건드리지 않는다."""
    n = 0
    for row in load_pairs_csv(path):
        cur = conn.execute(
            "INSERT INTO control_pair (pair_key, as_of, winner_label, "
            " loser_label, area_band, hypothesis, note) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(pair_key) DO NOTHING",
            (row["pair_key"], row["as_of"], row["winner_label"],
             row["loser_label"], row["area_band"], row["hypothesis"],
             row.get("note") or None))
        n += cur.rowcount
    return n


def all_pairs(conn: sqlite3.Connection) -> list[Pair]:
    rows = conn.execute(
        "SELECT * FROM control_pair ORDER BY as_of, pair_key").fetchall()
    return [Pair(r["pair_key"], r["as_of"], r["winner_label"], r["loser_label"],
                 r["area_band"], r["hypothesis"], r["winner_id"], r["loser_id"],
                 r["entry_price_gap"], r["outcome_gap"]) for r in rows]


def resolve(conn: sqlite3.Connection, resolver, *, as_of) -> list[str]:
    """라벨을 실제 단지에 붙인다. 못 붙이면 **추측하지 않고** 남긴다."""
    unresolved: list[str] = []
    for pair in all_pairs(conn):
        if pair.resolved:
            continue
        ids = {}
        for role, label in (("winner_id", pair.winner_label),
                            ("loser_id", pair.loser_label)):
            res = resolver(conn, label, as_of=as_of)
            if res is None:
                unresolved.append(f"{pair.pair_key}: '{label}' 을 못 찾았습니다")
                continue
            ids[role] = res
        if len(ids) == 2:
            conn.execute(
                "UPDATE control_pair SET winner_id=?, loser_id=? "
                " WHERE pair_key=?",
                (ids["winner_id"], ids["loser_id"], pair.pair_key))
    return unresolved


def discriminates(scores: dict[int, float], pair: Pair) -> tuple[bool | None, str]:
    """모델이 Winner 를 Loser 보다 높게 봤는가 (§27).

    **못 봤으면 실패가 아니라 '모름' 이다.** 데이터가 없어서 점수가 안 나온 것과
    모델이 틀린 것은 다르다.
    """
    if not pair.resolved:
        return None, f"{pair.pair_key}: 단지 매칭이 안 돼 판정할 수 없습니다"
    w = scores.get(pair.winner_id)
    l = scores.get(pair.loser_id)
    if w is None or l is None:
        missing = pair.winner_label if w is None else pair.loser_label
        return None, f"{pair.pair_key}: '{missing}' 점수가 없어 판정 불가"
    if w > l:
        return True, (f"{pair.pair_key}: Winner {w:.1f} > Loser {l:.1f} — "
                      f"구분했습니다")
    return False, (f"{pair.pair_key}: Winner {w:.1f} ≤ Loser {l:.1f} — "
                   f"구분하지 못했습니다. 왜 갈렸는지 설명할 Feature 가 "
                   f"모델에 없다는 뜻입니다")
