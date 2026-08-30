"""금액·면적·비율의 단일 단위계.

기존 토지 코드는 만원(`land_trade.deal_amount`)과 원(`auction_candidate.min_bid`)이
섞여 있고 변환이 호출부에 흩어져 있다(`pb.price_per_pyeong(min_bid / 10000.0, area)`).
토지는 그 정도로 버텼지만 아파트는 억·만원·원이 섞이는 데다 세금·대출·IRR까지
얽혀서, 단위 실수 하나가 IRR을 100배 틀리게 만든다.

규약 — 예외 없음:

  * 저장·계산의 **금액은 전부 원(₩), 파이썬 int**.
  * 저장·계산의 **면적은 전부 ㎡, float**.
  * 저장·계산의 **비율은 전부 0~1, float** (퍼센트가 아니다).
  * 억·만원·평·퍼센트는 **입력 파싱과 화면 출력에서만** 등장한다.

엔진 함수 본문에 `/ 10000` 이나 `* 3.3058` 같은 숫자가 보이면 리뷰에서 반려한다.
그 변환은 전부 이 모듈을 거친다.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import NewType

# 금액은 원 단위 int. NewType 이라 런타임 비용은 없고, 시그니처에서 의도가 드러난다.
Won = NewType("Won", int)

WON_PER_MANWON = 10_000
WON_PER_EOK = 100_000_000

# 기존 analysis/ 모듈들과 같은 값을 쓴다(계산 결과가 갈리지 않게).
PYEONG_M2 = 3.3058


# ─────────────────────────── 금액: 입력 파싱 ───────────────────────────

def _quantize(value: float | int | str | Decimal, factor: int) -> Won:
    """2진 부동소수 오차 없이 반올림한다.

    `11.2 * 1e8` 은 1119999999.9999999 가 되지만, Decimal(str(...)) 을 거치면
    정확히 1120000000 이 나온다. 금액에서 1원 오차는 테스트를 흔들리게 만든다.
    """
    d = Decimal(str(value)) * factor
    return Won(int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def from_eok(value: float | int | str) -> Won:
    """억 → 원. `from_eok(11.2) == 1_120_000_000`"""
    return _quantize(value, WON_PER_EOK)


def from_manwon(value: float | int | str) -> Won:
    """만원 → 원. 국토부 실거래가 API 의 거래금액 단위가 만원이다."""
    return _quantize(value, WON_PER_MANWON)


def won_round(value: float | int | str) -> Won:
    """계산 결과(float)를 원 단위 int 로 확정. 세율·이자 계산의 마지막 단계에 쓴다."""
    return _quantize(value, 1)


def as_won(value: object) -> Won:
    """이미 원 단위 int 임을 확인하는 게이트. float 이 흘러들어오면 여기서 막힌다.

    bool 은 int 의 서브클래스라 별도로 거른다 — `as_won(True)` 가 1원이 되면 안 된다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"금액은 원 단위 int 여야 합니다(받은 값: {value!r}, 타입: {type(value).__name__}). "
            f"만원/억이면 from_manwon()/from_eok(), 계산 결과면 won_round() 를 쓰세요."
        )
    return Won(value)


# ─────────────────────────── 금액: 출력 변환 ───────────────────────────

def to_eok(won: int) -> float:
    return won / WON_PER_EOK


def to_manwon(won: int) -> float:
    return won / WON_PER_MANWON


def _trim(text: str) -> str:
    """'11.20' → '11.2', '100.00' → '100'. 소수점이 있을 때만 깎는다."""
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_eok(won: int, digits: int = 2) -> str:
    """`fmt_eok(1_120_000_000) == '11.2억'`"""
    return _trim(f"{to_eok(won):,.{max(digits, 1)}f}") + "억"


def fmt_manwon(won: int, digits: int = 0) -> str:
    """`fmt_manwon(112_000_000) == '11,200만원'`"""
    return _trim(f"{to_manwon(won):,.{max(digits, 1)}f}") + "만원"


def fmt_won(won: int) -> str:
    return f"{won:,}원"


# ─────────────────────────────── 면적 ───────────────────────────────

def to_pyeong(area_m2: float) -> float:
    return area_m2 / PYEONG_M2


def from_pyeong(pyeong: float) -> float:
    return pyeong * PYEONG_M2


def fmt_m2(area_m2: float, digits: int = 2) -> str:
    return _trim(f"{area_m2:,.{max(digits, 1)}f}") + "㎡"


def fmt_pyeong(area_m2: float, digits: int = 0) -> str:
    return f"{to_pyeong(area_m2):,.{digits}f}평"


# ─────────────────────────────── 비율 ───────────────────────────────

def from_pct(percent: float) -> float:
    """퍼센트 → 0~1 비율. `from_pct(7.1) == 0.071`"""
    return percent / 100.0


def to_pct(ratio: float) -> float:
    return ratio * 100.0


def fmt_pct(ratio: float, digits: int = 1, *, sign: bool = False) -> str:
    """`fmt_pct(0.071) == '7.1%'`, `fmt_pct(-0.033, sign=True) == '-3.3%'`"""
    spec = f"{'+' if sign else ''}.{digits}f"
    return format(to_pct(ratio), spec) + "%"
