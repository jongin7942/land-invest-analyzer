"""Feature 등록부 — 4 State · GATE/ALPHA/RISK · CORE 티어 (신규 지시서 §4·§44·§45).

이 파일 하나가 세 가지를 동시에 강제한다.

**§4 — 4 State.** 사용자에게 19개 Feature 를 그대로 보여주지 않는다.
    CHEAPNESS · MOVEMENT · SUSTAINABILITY · STRETCH
네 상태로 묶어서 보여주고, 개별 Feature 는 그 아래 진단으로 내린다.

**§45 — 중복 가산 금지.** 한 Feature 는 `role` 을 **하나만** 갖는다.
ALPHA 이면서 동시에 RISK 일 수 없다. 이 규칙이 왜 필요한지는 실제로 겪었다:

| Feature | 전에는 | 문제 |
|---|---|---|
| `entry_position` | `value` 모델 가점 **+** `kill.상대고평가` 배제 | 같은 신호가 순위와 생존을 두 번 움직임 |
| `discovery_lag` | `momentum` 감점 **+** `kill.급등후매수` | 〃 |
| `supply_ratio_2y` | `supply` 모델 **+** `kill.공급충격` | 〃 |
| `downside_defense` | `jeonse` 모델 **+** `kill.전세약화` | 〃 |
| `transaction_quality` | `risk` 모델 **+** `kill.거래질악화` | 〃 |

Kill 은 감점이 아니라 배제라 산술적 이중가산은 아니었지만, 실질적으로는
가중치가 두 번 걸린다. 이제 각 Feature 가 어느 쪽인지 여기서 못박고,
`tests/test_delta.py` 가 ALPHA 모델과 RISK 규칙의 입력이 겹치지 않는지 검사한다.

**§44 — CORE 축소.** 기존 7그룹 19개를 지우지 않는다. 전부 `DIAGNOSTIC` 으로
시작하고, 백테스트에서 여러 Fold 를 살아남은 것만 `CORE` 로 올라간다.
올리는 것은 사람이 아니라 `backtest/usefulness.py` 다 — 스키마 CHECK 가
`survived_folds >= 2 AND promoted_run IS NOT NULL` 을 요구한다.

    목표 CORE 개수 10~20개. 지금은 **0개** 다(백테스트가 아직 안 돌았다).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# §4 네 상태 + 보조 축
CHEAPNESS = "CHEAPNESS"
MOVEMENT = "MOVEMENT"
SUSTAINABILITY = "SUSTAINABILITY"
STRETCH = "STRETCH"
GATE = "GATE"
CONFIDENCE = "CONFIDENCE"
CONTEXT = "CONTEXT"

STATES = (CHEAPNESS, MOVEMENT, SUSTAINABILITY, STRETCH)
STATE_LABEL = {
    CHEAPNESS: "현재 가격이 실제로 저평가되어 있는가",
    MOVEMENT: "실제 구매력이 이 후보로 이동하기 시작했는가",
    SUSTAINABILITY: "현재 움직임이 지속될 수 있는가",
    STRETCH: "이미 기대가 가격에 과도하게 반영되었는가",
}

# §45 세 영역
ROLE_GATE = "GATE"
ROLE_ALPHA = "ALPHA"
ROLE_RISK = "RISK"
ROLE_CONFIDENCE = "CONFIDENCE"
ROLE_CONTEXT = "CONTEXT"

# §44 세 티어
CORE = "CORE"
DIAGNOSTIC = "DIAGNOSTIC"
RESEARCH = "RESEARCH"

# CORE 로 올리려면 이만큼의 Fold 를 살아남아야 한다(§44).
MIN_FOLDS_FOR_CORE = 2
CORE_TARGET = (10, 20)


@dataclass(frozen=True)
class Entry:
    feature_key: str
    state: str
    role: str
    higher_is_better: bool
    note: str
    legacy_group: str | None = None
    tier: str = DIAGNOSTIC

    @property
    def label(self) -> str:
        arrow = "높을수록 좋음" if self.higher_is_better else "낮을수록 좋음"
        return f"{self.feature_key} [{self.state}·{self.role}·{self.tier}] {arrow}"


def _e(key, state, role, higher, note, legacy=None, tier=DIAGNOSTIC) -> Entry:
    return Entry(key, state, role, higher, note, legacy, tier)


# ── 등록부 본문 ──────────────────────────────────────────────────────
#
# 기존 19개(legacy 표시) + 이번에 추가되는 것.
# **role 이 겹치지 않는다** 는 것이 이 표의 존재 이유다.
REGISTRY: dict[str, Entry] = {e.feature_key: e for e in (
    # ── CHEAPNESS (§4-A) ────────────────────────────────────────────
    _e("entry_position", CHEAPNESS, ROLE_ALPHA, False,
       "매수가 구간 안에서의 위치. 낮을수록 싸다", "entry"),
    _e("relative_gap", CHEAPNESS, ROLE_ALPHA, True,
       "비교단지와의 가격비율이 과거 정상에서 벌어진 정도(§10). "
       "양수면 예전보다 싸진 것이라 높을수록 좋다"),
    _e("price_stretch", CHEAPNESS, ROLE_ALPHA, False,
       "장기 정상가 대비 현재가 이탈률(§5). 전고점 대비가 아니다"),
    _e("recoverable_discount_ratio", CHEAPNESS, ROLE_ALPHA, True,
       "관측 할인 중 닫힐 것으로 보는 비율(§13). 구조적 할인은 뺀다"),
    _e("same_capital_value", CHEAPNESS, ROLE_ALPHA, True,
       "같은 자기자본으로 잡는 자산 크기(§24)", "entry"),

    # ── MOVEMENT (§4-B) ─────────────────────────────────────────────
    _e("p25_migration", MOVEMENT, ROLE_ALPHA, True,
       "하위 25% 거래가격대의 이동(§9). 바닥이 올라오는가"),
    _e("median_migration", MOVEMENT, ROLE_ALPHA, True,
       "중앙값 가격대의 이동(§9)"),
    _e("p75_migration", MOVEMENT, ROLE_ALPHA, True,
       "상위 25% 가격대의 이동(§9). 이것만 오르면 Weak Shift 다"),
    _e("band_shift_strength", MOVEMENT, ROLE_ALPHA, True,
       "P25·Median·P75 가 함께 올랐는가(§9 Strong/Weak Distribution Shift)"),
    _e("latent_movement", MOVEMENT, ROLE_ALPHA, True,
       "조용히 오래 바닥이 이동해 왔는가(§7)"),
    _e("visible_movement", MOVEMENT, ROLE_ALPHA, True,
       "거래량 회복과 밴드 이동이 눈에 보이는가(§7)"),
    _e("slope_persistence", MOVEMENT, ROLE_ALPHA, True,
       "3M·6M·12M·24M 기울기가 일관되게 양수인가(§8)"),
    _e("money_arrival_depth", MOVEMENT, ROLE_ALPHA, False,
       "생활권 사다리에서 돈이 몇 번째 칸까지 왔나(§15). 4 이후는 Chase"),
    _e("transaction_recovery", MOVEMENT, ROLE_ALPHA, True,
       "거래가 되살아나고 있는가(§17)"),
    _e("momentum_3m", MOVEMENT, ROLE_CONTEXT, True,
       "3개월 변화율. 단독으로 매수신호가 아니다(§39)", "momentum"),
    _e("momentum_6m", MOVEMENT, ROLE_CONTEXT, True,
       "6개월 변화율", "momentum"),
    _e("momentum_12m", MOVEMENT, ROLE_CONTEXT, True,
       "12개월 변화율", "momentum"),
    _e("flow_stage", MOVEMENT, ROLE_CONTEXT, True,
       "거래량 6단계. RISK 로도 쓰지 않는다 — Stage 분류의 입력이다", "flow"),
    _e("investigation_priority", MOVEMENT, ROLE_CONTEXT, True,
       "조사 우선순위. 매수신호가 아니다", "flow"),

    # ── SUSTAINABILITY (§4-C) ───────────────────────────────────────
    _e("downside_defense", SUSTAINABILITY, ROLE_ALPHA, True,
       "전세가 매매를 받쳐 주는 정도(§14). Upside 에 더하지 않는다", "jeonse"),
    _e("jeonse_ratio", SUSTAINABILITY, ROLE_CONTEXT, True,
       "전세가율 자체", "jeonse"),
    _e("jeonse_lead", SUSTAINABILITY, ROLE_ALPHA, True,
       "전세가 매매를 선행하는가", "jeonse"),
    _e("neighbour_confirmation", SUSTAINABILITY, ROLE_CONFIDENCE, True,
       "같은 생활권 비교단지가 함께 움직이는가(§10). **Alpha 가 아니라 신뢰도**"),
    _e("buyer_pool", SUSTAINABILITY, ROLE_ALPHA, True,
       "이 가격대를 살 수 있는 수요층의 두께"),
    # 수준이 아니라 '벌어지는 속도' 다. 역세권이라 비싼 것은 이미 값에 있어서
    # 알파가 아니고, 그 격차가 계속 벌어진다는 사실만 알파다(features/access.py).
    _e("station_access_drift", SUSTAINABILITY, ROLE_ALPHA, True,
       "역까지 거리 밴드의 연 추세 × 투자기간. 2008~2025년 자체 측정", "access"),
    _e("next_node_score", MOVEMENT, ROLE_ALPHA, True,
       "이미 오른 칸 바로 아래이면서 아직 초기인가(§16)"),
    _e("effective_supply_risk", SUSTAINABILITY, ROLE_RISK, False,
       "실질 경쟁 공급(§13). 공급 관련 감점은 여기 하나로 모은다"),
    _e("replacement_availability", SUSTAINABILITY, ROLE_ALPHA, False,
       "같은 돈으로 대체할 물건이 얼마나 있나. 많으면 가격이 안 오른다"),
    _e("reset_completion", SUSTAINABILITY, ROLE_CONFIDENCE, True,
       "과열→조정→…→거래회복 중 어디까지 왔나(§18). "
       "고점 대비 하락률이 아니라 순서를 본다"),

    _e("path_quality", CHEAPNESS, ROLE_CONFIDENCE, True,
       "같은 가격이라도 어떻게 왔는가(§19). 위쪽 수요·아래쪽 지지선 확인 여부"),

    # ── STRETCH (§4-D) — 높을수록 감점 ──────────────────────────────
    _e("runup_1y", STRETCH, ROLE_RISK, False, "1년 상승폭(§4-D)"),
    _e("runup_2y", STRETCH, ROLE_RISK, False, "2년 상승폭"),
    _e("runup_3y", STRETCH, ROLE_RISK, False, "3년 상승폭"),
    _e("acceleration_zone", STRETCH, ROLE_RISK, False,
       "가속도 구간(§6). 선형 가산이 아니라 역U — Extreme 은 감점"),
    _e("price_percentile", STRETCH, ROLE_RISK, False,
       "자기 역사 안에서의 가격 백분위"),
    _e("price_to_jeonse_stretch", STRETCH, ROLE_RISK, False,
       "전세 대비 매매가 얼마나 벌어졌나"),
    _e("leader_exhaustion", STRETCH, ROLE_RISK, False,
       "Relevant Leader 가 이미 소진됐는가"),
    _e("distribution_exhaustion", STRETCH, ROLE_RISK, False,
       "가격 분포가 더 밀어올릴 여지가 없는가"),
    _e("persistent_cheapness", STRETCH, ROLE_RISK, False,
       "오래 쌌는데 격차가 안 닫힘(§14). 저평가 근거가 아니라 감점이다"),
    _e("transmission_failure", STRETCH, ROLE_RISK, False,
       "Leader 상승이 전달되지 않았다(§12). 구조적 할인 가능성"),
    _e("discovery_lag", STRETCH, ROLE_RISK, False,
       "이미 오른 뒤 발견했는가(§40)", "momentum"),
    _e("price_acceleration", STRETCH, ROLE_CONTEXT, True,
       "원시 가속도. **직접 가산하지 않는다** — acceleration_zone 의 입력", "momentum"),

    # ── GATE (§45) ──────────────────────────────────────────────────
    _e("capital_efficiency", GATE, ROLE_GATE, True,
       "같은 돈으로 잡는 자산 크기. 게이트 통과 후 참고값", "entry"),
    _e("transaction_quality", GATE, ROLE_GATE, True,
       "거래 표본의 질(§45 DataQuality 최소선). **Alpha 가 아니다**", "flow"),

    # ── CONTEXT — 점수에 직접 들어가지 않는다 ────────────────────────
    _e("regime", CONTEXT, ROLE_CONTEXT, True,
       "시장 국면 7종. 가중치를 고르는 데 쓰고 점수에 더하지 않는다", "regime"),
    _e("supply_ratio_1y", CONTEXT, ROLE_CONTEXT, False,
       "1년 공급비율. effective_supply_risk 의 입력", "supply"),
    _e("supply_ratio_2y", CONTEXT, ROLE_CONTEXT, False,
       "2년 공급비율. 〃", "supply"),
    _e("supply_ratio_3y", CONTEXT, ROLE_CONTEXT, False,
       "3년 공급비율. 〃", "supply"),
    _e("supply_ratio_5y", CONTEXT, ROLE_CONTEXT, False,
       "5년 공급비율. 〃", "supply"),
    _e("supply_cliff", CONTEXT, ROLE_CONTEXT, True,
       "공급 절벽. 〃", "supply"),
    _e("catalyst_alpha", CONTEXT, ROLE_CONTEXT, True,
       "남은 호재 알파. §34 대로 전달경로로 재구성 예정", "catalyst"),
)}


class RegistryError(ValueError):
    """등록부 규칙 위반."""


def get(feature_key: str) -> Entry | None:
    return REGISTRY.get(feature_key)


def by_state(state: str) -> list[Entry]:
    if state not in (CHEAPNESS, MOVEMENT, SUSTAINABILITY, STRETCH,
                     GATE, CONFIDENCE, CONTEXT):
        raise RegistryError(f"모르는 State: {state}")
    return [e for e in REGISTRY.values() if e.state == state]


def by_role(role: str) -> list[Entry]:
    return [e for e in REGISTRY.values() if e.role == role]


def alpha_keys() -> list[str]:
    return sorted(e.feature_key for e in REGISTRY.values() if e.role == ROLE_ALPHA)


def risk_keys() -> list[str]:
    return sorted(e.feature_key for e in REGISTRY.values() if e.role == ROLE_RISK)


def gate_keys() -> list[str]:
    return sorted(e.feature_key for e in REGISTRY.values() if e.role == ROLE_GATE)


def core_keys(conn: sqlite3.Connection | None = None) -> list[str]:
    """CORE 로 승격된 Feature. 백테스트 전에는 **비어 있는 것이 정상이다**(§44)."""
    if conn is None:
        return sorted(e.feature_key for e in REGISTRY.values() if e.tier == CORE)
    rows = conn.execute(
        "SELECT feature_key FROM feature_registry WHERE tier='CORE' "
        " ORDER BY feature_key").fetchall()
    return [r[0] for r in rows]


def sync(conn: sqlite3.Connection) -> int:
    """등록부를 DB 에 반영한다. 티어는 **덮어쓰지 않는다** —
    백테스트가 올린 CORE 승격을 코드 배포가 되돌리면 안 된다."""
    n = 0
    for e in REGISTRY.values():
        conn.execute(
            "INSERT INTO feature_registry (feature_key, state, role, tier, "
            " higher_is_better, legacy_group, note) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(feature_key) DO UPDATE SET "
            " state=excluded.state, role=excluded.role, "
            " higher_is_better=excluded.higher_is_better, "
            " legacy_group=excluded.legacy_group, note=excluded.note",
            (e.feature_key, e.state, e.role, e.tier, int(e.higher_is_better),
             e.legacy_group, e.note))
        n += 1
    return n


def promote(conn: sqlite3.Connection, feature_key: str, *, run_key: str,
            folds: int) -> None:
    """백테스트 결과로 CORE 승격 (§44).

    사람이 부르는 함수가 아니다. `backtest/usefulness.py` 가 부른다.
    Fold 수가 모자라면 스키마 CHECK 가 거부한다.
    """
    if folds < MIN_FOLDS_FOR_CORE:
        raise RegistryError(
            f"'{feature_key}' 를 CORE 로 올리려면 Fold {MIN_FOLDS_FOR_CORE}개 이상을 "
            f"살아남아야 합니다 (지금 {folds}개). 한 시기에서만 맞는 Feature 는 "
            f"DIAGNOSTIC 에 둡니다(§44)")
    conn.execute(
        "UPDATE feature_registry SET tier='CORE', survived_folds=?, "
        " promoted_run=? WHERE feature_key=?", (folds, run_key, feature_key))


def demote(conn: sqlite3.Connection, feature_key: str, *, reason: str) -> None:
    """CORE 에서 내린다. 한 시기에서만 맞았던 Feature 를 방치하지 않는다."""
    conn.execute(
        "UPDATE feature_registry SET tier='DIAGNOSTIC', survived_folds=0, "
        " promoted_run=NULL, note=note || ' / 강등: ' || ? WHERE feature_key=?",
        (reason, feature_key))


def audit_roles() -> list[str]:
    """§45 위반 점검 — 한 Feature 가 두 역할을 갖지 않는지.

    dict 라 구조적으로 불가능하지만, 이 함수는 **ALPHA 와 RISK 가 같은 원천을
    두 번 세지 않는지**를 사람이 읽을 수 있게 보고한다.
    """
    problems: list[str] = []
    alpha = set(alpha_keys())
    risk = set(risk_keys())
    gate = set(gate_keys())
    for a, b, label in ((alpha, risk, "ALPHA·RISK"), (alpha, gate, "ALPHA·GATE"),
                        (risk, gate, "RISK·GATE")):
        both = a & b
        if both:
            problems.append(f"{label} 양쪽에 있는 Feature: {sorted(both)}")
    return problems


def summary() -> str:
    lines = [f"Feature 등록부 {len(REGISTRY)}개"]
    for state in (CHEAPNESS, MOVEMENT, SUSTAINABILITY, STRETCH, GATE, CONTEXT):
        entries = by_state(state)
        if not entries:
            continue
        lines.append(f"  {state} ({len(entries)}) — {STATE_LABEL.get(state, '')}")
        for e in sorted(entries, key=lambda x: x.feature_key):
            lines.append(f"    {e.label}")
    core = core_keys()
    lines.append(f"  CORE {len(core)}개 " +
                 ("(백테스트 전이라 비어 있는 것이 정상입니다)" if not core
                  else f"— {', '.join(core)}"))
    return "\n".join(lines)
