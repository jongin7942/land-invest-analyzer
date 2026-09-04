"""Ridge 회귀(순수 파이썬) + Walk-Forward 평가 + 잔차 분위수로 Bear/Base/Bull.

numpy 가 없는 환경이라 정규방정식을 가우스 소거로 푼다(변수 ≤ 25 개).
결측 변수는 학습 평균으로 채우지 않고 **행을 뺀다**(UNKNOWN ≠ 중간값). 대신 결측이 잦은 변수는
FEATURE_SETS 를 나눠 비교한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from apt_engine.exitprice.panel import Row
from apt_engine.relative.store import median, percentile


@dataclass
class Fit:
    features: list
    mean: dict
    std: dict
    beta: list           # [intercept, b1..bk] (표준화 공간)
    lam: float
    n: int
    resid_q: dict = field(default_factory=dict)      # {0.2: q20, 0.5: ..., 0.8: q80}

    def predict(self, x: dict) -> float | None:
        z = []
        for f in self.features:
            v = x.get(f)
            if v is None:
                return None
            z.append((v - self.mean[f]) / self.std[f] if self.std[f] > 0 else 0.0)
        return self.beta[0] + sum(b * zi for b, zi in zip(self.beta[1:], z))


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col] or 1e-12
        for r in range(n):
            if r != col:
                f = m[r][col] / p
                if f:
                    for k in range(col, n + 1):
                        m[r][k] -= f * m[col][k]
    return [m[i][n] / (m[i][i] or 1e-12) for i in range(n)]


@dataclass
class Design:
    """표준화된 설계행렬의 XᵀX·Xᵀy — λ 가 달라도 다시 만들지 않는다."""
    features: list
    mean: dict
    std: dict
    xtx: list
    xty: list
    data: list          # [(x, y)]


def design(rows: list[Row], features: list[str]) -> Design | None:
    data = [(r.x, r.target) for r in rows if r.target is not None and all(r.x.get(f) is not None for f in features)]
    if len(data) < 50:
        return None
    n = len(data)
    mean = {f: sum(x[f] for x, _ in data) / n for f in features}
    std = {f: math.sqrt(sum((x[f] - mean[f]) ** 2 for x, _ in data) / n) for f in features}
    k = len(features)
    xtx = [[0.0] * (k + 1) for _ in range(k + 1)]
    xty = [0.0] * (k + 1)
    for x, yi in data:
        xi = [1.0] + [((x[f] - mean[f]) / std[f] if std[f] > 0 else 0.0) for f in features]
        for i in range(k + 1):
            xi_i = xi[i]
            if xi_i == 0.0:
                continue
            xty[i] += xi_i * yi
            row = xtx[i]
            for j in range(i, k + 1):
                row[j] += xi_i * xi[j]
    for i in range(k + 1):
        for j in range(i + 1, k + 1):
            xtx[j][i] = xtx[i][j]
    return Design(features, mean, std, xtx, xty, data)


def fit_design(d: Design, lam: float) -> Fit:
    k = len(d.features)
    a = [row[:] for row in d.xtx]
    for i in range(1, k + 1):
        a[i][i] += lam
    beta = _solve(a, d.xty)
    f = Fit(d.features, d.mean, d.std, beta, lam, len(d.data))
    resid = [yi - f.predict(x) for x, yi in d.data]
    f.resid_q = {q: percentile(resid, q) for q in (0.1, 0.2, 0.5, 0.8, 0.9)}
    return f


def fit(rows: list[Row], features: list[str], lam: float = 1.0) -> Fit | None:
    d = design(rows, features)
    return fit_design(d, lam) if d else None


# ── 평가 ──

def spearman(pred: list[float], act: list[float]) -> float | None:
    n = len(pred)
    if n < 10:
        return None
    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rp, ra = ranks(pred), ranks(act)
    mp, ma = (n - 1) / 2, (n - 1) / 2
    num = sum((rp[i] - mp) * (ra[i] - ma) for i in range(n))
    den = math.sqrt(sum((rp[i] - mp) ** 2 for i in range(n)) * sum((ra[i] - ma) ** 2 for i in range(n)))
    return num / den if den else None


def evaluate(f: Fit, rows: list[Row]) -> dict:
    pairs = [(f.predict(r.x), r.target) for r in rows if r.target is not None]
    pairs = [(p, a) for p, a in pairs if p is not None]
    if len(pairs) < 10:
        return {"n": len(pairs)}
    pred = [p for p, _ in pairs]; act = [a for _, a in pairs]
    mae = sum(abs(p - a) for p, a in pairs) / len(pairs)
    base_mae = sum(abs(median(act) - a) for a in act) / len(act)     # 시장 중앙값으로만 찍었을 때
    ic = spearman(pred, act)
    # Winner Recall: 실제 상위 10% 중 예측 상위 20% 에 들어온 비율
    n = len(pairs)
    top_act = set(sorted(range(n), key=lambda i: -act[i])[: max(1, n // 10)])
    top_pred = set(sorted(range(n), key=lambda i: -pred[i])[: max(1, n // 5)])
    recall = len(top_act & top_pred) / len(top_act)
    top_pred30 = set(sorted(range(n), key=lambda i: -pred[i])[: max(1, int(n * 0.3))])
    recall30 = len(top_act & top_pred30) / len(top_act)
    # 예측 상위 10% 의 실제 중앙값 vs 전체 중앙값, 그리고 예측 상위 20% 안에서 실제 상위 절반(중앙값 이상)인 비율
    p10 = sorted(range(n), key=lambda i: -pred[i])[: max(1, n // 10)]
    lift = median([act[i] for i in p10]) - median(act)
    med = median(act)
    precision_half = sum(1 for i in top_pred if act[i] >= med) / len(top_pred)
    return {"n": n, "mae": round(mae, 4), "mae_market_only": round(base_mae, 4), "ic": round(ic, 3) if ic is not None else None,
            "winner_recall": round(recall, 3), "winner_recall30": round(recall30, 3),
            "precision_above_median": round(precision_half, 3), "top_decile_lift": round(lift, 4)}


def walk_forward(rows: list[Row], features: list[str], test_years: list[int],
                 lams: list[float] = (1.0,)) -> dict[float, dict]:
    """test year T: 학습 = 진입연도 ≤ T−5 (결과가 T 이전에 확정된 창만). λ 별 결과를 한 번의 설계행렬로."""
    out: dict[float, dict] = {lam: {} for lam in lams}
    by_year: dict[int, list[Row]] = {}
    for r in rows:
        by_year.setdefault(int(r.entry_ym[:4]), []).append(r)
    for T in test_years:
        train = [r for y, rs in by_year.items() if y <= T - 5 for r in rs]
        test = by_year.get(T, [])
        d = design(train, features)
        for lam in lams:
            if d is None or not test:
                out[lam][T] = {"n_train": len(train), "note": "학습 표본 부족"}
                continue
            f = fit_design(d, lam)
            ev = evaluate(f, test)
            ev["n_train"] = f.n
            ev["coef"] = {fe: round(b, 4) for fe, b in zip(features, f.beta[1:])}
            out[lam][T] = ev
    return out
