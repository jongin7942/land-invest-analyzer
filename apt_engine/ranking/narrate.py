"""왜 이 순위인지 사람 말로 설명한다 (지시서 §36·§37).

**잘못된 설명**

    Early Emerging + High Remaining Alpha + Low Stretch

이건 설명이 아니라 변수명 나열이다. 읽는 사람은 이 단지를 왜 사야
하는지 하나도 모른 채 순위만 믿게 된다.

**좋은 설명**

    최근 거래가 늘면서 실제 거래가격의 아래쪽도 조금씩 올라가고 있습니다.
    주변 선호 단지는 이미 가격이 많이 올랐지만 이 단지는 아직 가격 차이가
    큽니다. 전세가격도 매매가격을 어느 정도 받쳐주고 있어 하락 위험에 비해
    상승 가능성이 더 크다고 판단합니다. 다만 4억원을 넘겨 사면 이런 가격
    장점이 빠르게 줄어듭니다.

**설계 원칙**

  · 없는 것은 말하지 않는다. Feature 가 unusable 이면 그 문장을 뺀다.
    "확인되지 않았다" 를 "없다" 로 바꿔 쓰지 않는다.
  · 전문용어를 쓰지 않는다. 내부 변수명은 §37 사전으로 번역한다.
  · 마지막에 **가격 조건**을 반드시 붙인다. 좋은 아파트가 아니라
    좋은 가격에 사는 것이 결론이기 때문이다(§2).
"""
from __future__ import annotations

# §37 내부 지표 → 사용자 문구. 내부 이름은 그대로 두고 화면에서만 바꾼다.
TERM = {
    "cheapness": "현재 얼마나 싼가",
    "entry_position": "현재 얼마나 싼가",
    "movement": "가격이 움직이기 시작했나",
    "visible_movement": "가격이 움직이기 시작했나",
    "latent_movement": "움직일 조짐이 있나",
    "remaining_alpha": "앞으로 오를 여력이 얼마나 남았나",
    "price_stretch": "이미 너무 많이 올랐나",
    "relative_stretch": "주변 대비 비싼가",
    "buyer_pool": "앞으로 이 집을 살 사람이 충분한가",
    "income_flow": "이 지역에 돈이 들어오고 있나",
    "migration_flow": "위 동네에서 돈이 내려오고 있나",
    "accessible_money_flow": "그 돈이 실제로 이 집까지 닿는가",
    "transmission_failure": "주변 인기 아파트 상승이 이곳까지 번질까",
    "downside_defense": "가격이 떨어질 때 얼마나 버틸 수 있나",
    "effective_supply_risk": "주변 새 아파트 공급이 부담인가",
    "capital_efficiency": "가진 돈을 얼마나 효율적으로 쓰는가",
    "exit_liquidity": "나중에 팔기 쉬운가",
    "confidence": "이 판단을 얼마나 믿을 수 있는가",
}


def term(key: str) -> str:
    return TERM.get(key, key)


def _f(fs, key):
    """쓸 수 있는 feature 만 돌려준다. 없으면 None — 문장을 아예 안 쓴다."""
    try:
        f = fs[key]
    except Exception:
        return None
    return f if getattr(f, "usable", False) else None


def _eok(won):
    from apt_engine import units
    return units.fmt_eok(won)


def sentences(fs, *, bands=None, price: int | None = None,
              gate=None) -> list[str]:
    """후보 하나를 3~6문장으로 설명한다.

    fs     FeatureSet
    bands  executable.PriceBands (가격 조건 문장을 만든다)
    gate   regulation.gate.Decision (막혔으면 그 이유가 첫 문장)
    """
    out: list[str] = []

    # 0) 못 사는 물건이면 그 말이 먼저다. 좋은 이유를 앞에 두면
    #    읽는 사람이 살 수 있는 줄 안다.
    if gate is not None and not getattr(gate, "executable", True):
        out.append(f"먼저, 이 물건은 지금 조건으로는 매수할 수 없습니다. {gate.reason}")

    # 1) 왜 싼가
    ep = _f(fs, "entry_position")
    rel = _f(fs, "relative_stretch")
    if ep is not None and ep.value is not None and ep.value < 0.5:
        out.append("지금 가격은 이 단지가 최근 몇 해 동안 거래되던 범위에서 "
                   "아래쪽에 있습니다.")
    if rel is not None and rel.value is not None and rel.value < -0.05:
        out.append(f"주변 비슷한 단지와 견줘도 {abs(rel.value):.0%}가량 쌉니다. "
                   f"상품성 차이를 감안하고 남은 격차입니다.")

    # 2) 실제로 움직이고 있는가
    vis = _f(fs, "visible_movement")
    lat = _f(fs, "latent_movement")
    if vis is not None and vis.value and vis.value > 0:
        out.append("최근 거래가 늘면서 실제 거래가격의 아래쪽도 조금씩 "
                   "올라가고 있습니다.")
    elif lat is not None and lat.value and lat.value > 0:
        out.append("아직 가격이 뚜렷하게 오르진 않았지만 거래가 늘고 매물이 "
                   "줄어드는 조짐이 보입니다.")

    # 3) 어떤 돈이 들어오는가 — 두 종류를 구분해서 말한다 (§8)
    inc = _f(fs, "income_flow")
    mig = _f(fs, "migration_flow")
    if inc is not None and inc.value and inc.value > 0:
        out.append("이 지역 자체의 일자리와 소득이 늘고 있어 동네 사람들의 "
                   "구매력이 커지고 있습니다.")
    if mig is not None and mig.value and mig.value > 0:
        out.append("위 동네 가격이 많이 올라서, 거기 사려던 사람들이 이쪽으로 "
                   "내려올 여지가 생겼습니다. 다만 위 동네가 멈추면 이 흐름도 "
                   "같이 멈춥니다.")

    # 4) 전세가 받쳐주는가
    dd = _f(fs, "downside_defense")
    if dd is not None and dd.value and dd.value > 0.5:
        out.append("전세가격이 매매가격을 어느 정도 받쳐주고 있어 하락 위험에 "
                   "비해 상승 가능성이 더 크다고 봅니다.")

    # 5) 위험
    sup = _f(fs, "effective_supply_risk")
    if sup is not None and sup.value and sup.value > 0.5:
        out.append("다만 주변에 새 아파트 입주가 예정돼 있어 한동안 가격이 "
                   "눌릴 수 있습니다.")
    tf = _f(fs, "transmission_failure")
    if tf is not None and tf.value and tf.value > 0.5:
        out.append("주변 인기 단지가 올랐는데도 이 단지가 오래 따라 오르지 "
                   "못한 적이 있습니다. 싼 데는 이유가 있을 수 있습니다.")

    # 6) 가격 조건 — 항상 마지막. 결론은 '좋은 아파트' 가 아니라 '좋은 가격' 이다.
    if bands is not None and getattr(bands, "chase", None):
        out.append(f"{_eok(bands.chase)}을 넘겨 사면 이런 가격 장점이 "
                   f"빠르게 줄어듭니다.")
    elif bands is not None and getattr(bands, "do_not_buy", None):
        out.append(f"{_eok(bands.do_not_buy)} 이상에서는 사지 않는 것이 좋습니다.")

    if not out:
        out.append("이 단지를 설명할 만큼의 데이터가 아직 모이지 않았습니다. "
                   "순위에 올랐더라도 근거가 얇다는 뜻입니다.")
    return out


def paragraph(fs, **kw) -> str:
    return " ".join(sentences(fs, **kw))


# ── §40 대출 가능 여부 5단계 ──────────────────────────────────────────
#
# 숫자 하나(필요현금 3.2억)만 주면 "내가 살 수 있나" 에 답이 안 된다.
# 소득·기존대출을 모르면 **한도를 지어내지 않고** 그 사실을 표시한다.
EASY = "여유 있게 접근 가능"
WITH_LOAN = "대출 활용 시 접근 가능"
NEEDS_CHECK = "대출조건 확인 필요"
TIGHT = "현재 자기자본으로 무리"
IMPOSSIBLE = "실행 불가"
NEEDS_FINANCE_DATA = "NEEDS_USER_FINANCE_DATA"

FEASIBILITY_ORDER = (EASY, WITH_LOAN, NEEDS_CHECK, TIGHT, IMPOSSIBLE)


def loan_feasibility(*, cash: int | None, required_no_loan: int | None,
                     required_with_loan: int | None,
                     has_income_data: bool,
                     unknown_costs: int = 0) -> tuple[str, str]:
    """(라벨, 설명). 자기자본이 적을수록 이 구분이 결과를 좌우한다(§39·§40)."""
    if cash is None:
        return NEEDS_FINANCE_DATA, "투자금을 넣어야 판단할 수 있습니다"
    if required_no_loan is None and required_with_loan is None:
        return NEEDS_FINANCE_DATA, "매수 부대비용 규칙이 없어 필요 현금을 못 냅니다"

    if unknown_costs:
        # 확인 불가 항목이 있으면 실제 필요 현금은 더 크다. 그 상태에서
        # '가능' 이라고 말하면 못 사는 집이 살 수 있는 집으로 보인다.
        return NEEDS_CHECK, (f"확인 불가 항목 {unknown_costs}개가 있어 실제 필요 "
                             f"현금은 더 큽니다")

    if required_no_loan is not None and cash >= required_no_loan:
        return EASY, "대출 없이도 자기자본으로 감당됩니다"

    if not has_income_data:
        return NEEDS_FINANCE_DATA, ("연소득과 기존 대출을 모르면 대출 한도를 "
                                    "확정할 수 없습니다. 임의로 정하지 않습니다.")

    if required_with_loan is not None and cash >= required_with_loan:
        return WITH_LOAN, "대출을 쓰면 접근 가능합니다"

    if required_with_loan is not None and cash >= required_with_loan * 0.85:
        return TIGHT, "조금 모자랍니다. 무리해서 맞추면 이자 부담이 커집니다."

    return IMPOSSIBLE, "지금 자기자본으로는 실행할 수 없습니다"


__all__ = ["sentences", "paragraph", "term", "TERM", "loan_feasibility",
           "EASY", "WITH_LOAN", "NEEDS_CHECK", "TIGHT", "IMPOSSIBLE",
           "NEEDS_FINANCE_DATA", "FEASIBILITY_ORDER"]
