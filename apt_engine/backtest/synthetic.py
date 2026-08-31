"""합성 시장 — **하네스를 검증하기 위한 것이지 시장을 예측하기 위한 것이 아니다.**

실거래 데이터가 아직 없는 동안(§B 데이터 공백) 백테스트 하네스가 실제로 도는지,
KPI 가 옳게 계산되는지, 누출 검사가 진짜로 누출을 잡는지 확인해야 한다.
그러려면 **정답을 이미 아는 시장**이 필요하다.

여기서 만드는 시장에는 일부러 **진짜 규칙 하나**를 심는다. 어떤 규칙을 심을지
고를 수 있고, 이게 이 모듈의 핵심이다.

    MOMENTUM     오르던 단지가 계속 오른다 (추세 시장)
    MEAN_REVERT  지역 평균보다 싼 단지가 되돌아온다 (가치 시장)
    NONE         아무 규칙도 없다 (귀무가설 — 하네스가 헛것을 보는지 확인)

두 시장을 각각 돌려서 §74 가 실제로 작동하는지 본다.

  * MOMENTUM 시장에서 momentum 모델이 가중치를 받는가
  * MEAN_REVERT 시장에서는 **반대로** value 쪽이 받는가
  * NONE 시장에서 아무 모델도 가중치를 못 받는가
  * 미래를 심으면 누출 검사가 잡는가

같은 코드가 시장에 따라 다른 답을 내야 한다. 항상 같은 답이 나오면 그건
데이터를 읽는 게 아니라 내가 미리 정해 둔 답을 되풀이하는 것이다.

**금지사항** (지시서 §0):
  * 여기서 나온 숫자를 실제 시세처럼 보여주지 않는다.
  * 합성 데이터는 실제 DB 파일에 쓰지 않는다 — `require_scratch()` 가 막는다.
  * 이 데이터로 돈 백테스트의 `market_source` 는 항상 'SYNTHETIC' 이다.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
from dataclasses import dataclass

import config
from apt_engine import ENGINE_VERSION

MARKET_SOURCE = "SYNTHETIC"

# 심을 수 있는 규칙
MOMENTUM = "MOMENTUM"
MEAN_REVERT = "MEAN_REVERT"
NONE = "NONE"
RULES = (MOMENTUM, MEAN_REVERT, NONE)
RULE_NOTE = {
    MOMENTUM: "오르던 단지가 계속 오른다",
    MEAN_REVERT: "지역 평균보다 싼 단지가 되돌아온다",
    NONE: "아무 규칙도 없다 (귀무가설)",
}

# gap 이 되돌아오는 속도. 1 에 가까울수록 천천히 되돌아온다.
REVERT_RHO = 0.90
# MOMENTUM 시장에서 단지 고유 추세의 월간 크기(잠재 가치 1 기준).
# 2년(24개월) 보유면 signal × value × 24 × 이 값 만큼 벌어진다.
# 같은 기간 누적 잡음의 표준편차(WALK_SIGMA × √24)보다 확실히 커야
# 추세가 예측력을 갖는 시장이 된다.
TREND_PER_MONTH = 0.015
# 단지 고유 변동의 월간 표준편차 배율 (noise 대비)
WALK_SIGMA_FRACTION = 1 / 12

# 심는 규칙의 세기. 0 이면 아무 신호도 없는 시장(귀무가설 검증용)이 된다.
DEFAULT_SIGNAL = 0.35
# 신호를 덮는 잡음. 실제 시장은 신호보다 잡음이 크다.
DEFAULT_NOISE = 0.12


class SyntheticDataError(RuntimeError):
    """합성 데이터를 실제 DB 에 쓰려고 했다."""


def require_scratch(db_path: str | None) -> None:
    """실제 DB 파일에는 합성 데이터를 넣지 못하게 한다.

    한 번 섞이면 어느 행이 진짜인지 구분할 방법이 없다. 지우는 것보다 막는 게 싸다.
    """
    if db_path in (None, ":memory:"):
        return
    real = {str(getattr(config, name, "")) for name in
            ("APT_DB_PATH", "DB_PATH")}
    if str(db_path) in real:
        raise SyntheticDataError(
            f"합성 데이터를 실제 DB({db_path})에 쓸 수 없습니다. "
            f"메모리 DB 나 임시 파일을 쓰세요 — 한 번 섞이면 어느 행이 실제 "
            f"거래인지 구분할 수 없게 됩니다")


@dataclass(frozen=True)
class Market:
    """만들어진 시장의 명세. 채점할 때 '정답의 정답' 으로 쓴다."""
    complex_ids: list[int]
    area_band: str
    start_ym: str
    end_ym: str
    signal: float
    noise: float
    seed: int
    # 단지 → 심어 놓은 잠재 가치(=이후 상승폭을 결정한 값). 검증 전용.
    latent: dict[int, float]
    rule: str = MOMENTUM

    @property
    def label(self) -> str:
        return (f"합성 시장[{self.rule}: {RULE_NOTE[self.rule]}] · "
                f"단지 {len(self.complex_ids)}개 · "
                f"{self.start_ym}~{self.end_ym} · 신호 {self.signal:.2f} · "
                f"잡음 {self.noise:.2f} · seed {self.seed}  "
                f"⚠ 실제 시세가 아닙니다")


def build(conn: sqlite3.Connection, *, n_complexes: int = 40,
          start_ym: str = "201501", end_ym: str = "202512",
          area_band: str = "84", lawd_cds: tuple[str, ...] = ("11110", "41110"),
          seed: int = 20260831, signal: float = DEFAULT_SIGNAL,
          noise: float = DEFAULT_NOISE,
          base_price: int = 600_000_000,
          rule: str = MOMENTUM) -> Market:
    """월별 가격 스냅샷이 있는 합성 시장을 만든다."""
    if rule not in RULES:
        raise ValueError(f"모르는 규칙: {rule} (가능: {', '.join(RULES)})")
    rng = random.Random(seed)

    for i, lawd in enumerate(lawd_cds):
        conn.execute(
            "INSERT INTO region (lawd_cd, sido, name) VALUES (?,?,?) "
            "ON CONFLICT(lawd_cd) DO NOTHING",
            (lawd, "합성", f"합성구{i + 1}"))

    months = _month_range(start_ym, end_ym)
    # 지역 공통 흐름. 상승·하락·횡보가 섞이게 만든다 —
    # §56 이 요구하는 2022~2023 형태의 하락 구간이 있어야 하네스를 검증할 수 있다.
    market_path = _market_path(months, rng)

    ids: list[int] = []
    latent: dict[int, float] = {}

    for k in range(n_complexes):
        lawd = lawd_cds[k % len(lawd_cds)]
        cur = conn.execute(
            "INSERT INTO complex (kapt_code, name, name_norm, lawd_cd, "
            " emd_name, apt_households, approval_year, confidence) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"SYN{seed}{k:04d}", f"합성{k:03d}", f"합성{k:03d}", lawd,
             "합성동", 300 + rng.randrange(0, 900),
             1990 + rng.randrange(0, 30), "HIGH"))
        cid = int(cur.lastrowid)
        ids.append(cid)

        # 잠재 가치: 관측되지 않는 단지 고유의 힘.
        value = rng.gauss(0, 1)
        latent[cid] = value

        base = base_price * (1 + rng.gauss(0, noise))
        gap = rng.gauss(0, noise)          # 지역 평균 대비 현재 격차 (로그 스케일)
        walk = 0.0                         # 단지 고유의 누적 변동 (로그 스케일)
        trend = signal * value * TREND_PER_MONTH   # 단지 고유의 월간 추세

        for idx, ym in enumerate(months):
            # **곱셈으로 쌓아야 한다.** 덧셈 잡음을 곱셈 추세에 더하면,
            # 같은 금액이 올라도 싼 단지의 **수익률**이 자동으로 높아진다.
            # 그러면 신호를 0 으로 넣은 시장에서도 "싼 게 더 오른다" 가 나와서
            # value 모델이 IC +0.13 을 받는다. (NONE 모드에서 실제로 관측했다)
            walk += rng.gauss(0, noise * WALK_SIGMA_FRACTION)

            if rule == MOMENTUM:
                # 단지마다 고정 추세 → 지난 추세가 다음 추세를 예측한다
                own = trend * idx + walk
            elif rule == MEAN_REVERT:
                # 격차가 되돌아온다 → **지금 싼 단지**가 이후 더 오른다.
                # 되돌아오기 전에 새 격차가 생기므로 추세는 예측력을 잃는다.
                gap = REVERT_RHO * gap + rng.gauss(0, noise * (1 - REVERT_RHO) * 3)
                own = gap
            else:                          # NONE — 순수 곱셈 랜덤워크
                own = walk

            level = base * math.exp(own) * (1 + market_path[idx])
            level = max(int(level), 10_000_000)
            _insert_snapshot(conn, cid, area_band, ym, level, rng)
            _insert_jeonse(conn, cid, area_band, ym, level, rng)

    conn.commit()
    return Market(ids, area_band, start_ym, end_ym, signal, noise, seed, latent,
                  rule)


def _insert_snapshot(conn, cid: int, band: str, ym: str, price: int,
                     rng: random.Random) -> None:
    sample = 4 + rng.randrange(0, 12)
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, "
        " window_months, representative_price, method, sample_n, confidence, "
        " price_p25, price_p50, price_p75, engine_version, data_grade, "
        " calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT DO NOTHING",
        (cid, band, ym, 3, price, "median", sample,
         "HIGH" if sample >= 10 else "MEDIUM",
         int(price * 0.95), price, int(price * 1.06), ENGINE_VERSION,
         "CONFIRMED",
         json.dumps({"합성": True, "주의": "실제 거래가 아닙니다"},
                    ensure_ascii=False)))


def _insert_jeonse(conn, cid: int, band: str, ym: str, price: int,
                   rng: random.Random) -> None:
    ratio = min(0.85, max(0.40, rng.gauss(0.60, 0.08)))
    deposit = int(price * ratio)
    sample = 3 + rng.randrange(0, 8)
    conn.execute(
        "INSERT INTO jeonse_snapshot (complex_id, area_band, as_of_ym, "
        " window_months, representative_deposit, method, sample_n, confidence, "
        " jeonse_ratio, engine_version, data_grade, calc_trace) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (cid, band, ym, 3, deposit, "median", sample,
         "HIGH" if sample >= 10 else "MEDIUM", ratio, ENGINE_VERSION,
         "CONFIRMED",
         json.dumps({"합성": True}, ensure_ascii=False)))


def _market_path(months: list[str], rng: random.Random) -> list[float]:
    """지역 공통 가격 경로. 상승기 → 과열 → 하락 → 회복 을 한 번 이상 만든다."""
    out: list[float] = []
    level = 0.0
    n = len(months)
    for i in range(n):
        phase = i / max(1, n - 1)
        if phase < 0.55:
            step = 0.006                    # 완만한 상승
        elif phase < 0.70:
            step = -0.012                   # 하락 구간 (§56)
        else:
            step = 0.004                    # 회복
        level += step + rng.gauss(0, 0.004)
        out.append(level)
    return out


def plant_leak(conn: sqlite3.Connection, market: Market, *,
               as_of_ym: str, boost: float = 0.5) -> list[int]:
    """일부러 누출을 심는다 — 누출 검사가 진짜로 잡는지 확인하기 위해서다.

    컷오프 **이후** 달의 가격을, 나중에 크게 오를 단지들만 골라 미리 부풀린다.
    누출 검사가 이걸 못 잡으면 그 검사는 아무 것도 보장하지 않는다.

    테스트에서만 쓴다. 프로덕션 경로에서 호출되면 안 된다.
    """
    winners = sorted(market.latent, key=lambda c: -market.latent[c])[:5]
    for cid in winners:
        conn.execute(
            "UPDATE price_snapshot SET representative_price = "
            " CAST(representative_price * ? AS INTEGER) "
            " WHERE complex_id=? AND as_of_ym > ?",
            (1 + boost, cid, as_of_ym))
    conn.commit()
    return winners


def _month_range(start_ym: str, end_ym: str) -> list[str]:
    a = int(start_ym[:4]) * 12 + int(start_ym[4:6]) - 1
    b = int(end_ym[:4]) * 12 + int(end_ym[4:6]) - 1
    return [f"{t // 12:04d}{t % 12 + 1:02d}" for t in range(a, b + 1)]
