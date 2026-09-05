"""순수 파이썬 히스토그램 그래디언트 부스팅(깊이 2, 결측 인지) — Exit Price 모델 후보.

numpy 없이 돌아가도록 열(column) 단위 정수 bin 으로 미리 나눠 놓고, 라운드마다
노드별 (feature, bin) 히스토그램만 만든다. 결측값은 별도 bin(=B) 으로 두고 분할 때
왼쪽/오른쪽 어느 쪽으로 보낼지도 함께 고른다 → 결측 때문에 행을 버리지 않는다.
손실은 제곱오차(목표는 연도 중앙값을 뺀 log 수익 또는 연도 내 백분위).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from apt_engine.exitprice.panel import Row
from apt_engine.relative.store import percentile

NB = 24          # 값 bin 수(분위) — 결측은 NB 번째 bin


@dataclass
class Node:
    feat: int = -1
    thr: int = 0                 # bin <= thr → left
    miss_left: bool = True
    left: "Node | None" = None
    right: "Node | None" = None
    value: float = 0.0


@dataclass
class Boost:
    features: list[str]
    cuts: dict[str, list[float]]
    trees: list[Node] = field(default_factory=list)
    base: float = 0.0
    lr: float = 0.1
    n: int = 0

    def _bin(self, f: str, v) -> int:
        if v is None:
            return NB
        c = self.cuts[f]
        lo, hi = 0, len(c)
        while lo < hi:
            m = (lo + hi) // 2
            if v <= c[m]:
                hi = m
            else:
                lo = m + 1
        return lo

    def predict(self, x: dict) -> float | None:
        b = [self._bin(f, x.get(f)) for f in self.features]
        y = self.base
        for t in self.trees:
            n = t
            while n.left is not None:
                bi = b[n.feat]
                go_left = (n.miss_left if bi == NB else bi <= n.thr)
                n = n.left if go_left else n.right
            y += n.value
        return y


def _quantile_cuts(vals: list[float]) -> list[float]:
    v = sorted(vals)
    if not v:
        return []
    cuts = []
    for q in range(1, NB):
        c = percentile(v, q / NB)
        if not cuts or c > cuts[-1]:
            cuts.append(c)
    return cuts


def _best_split(cols: list[list[int]], idx: list[int], g: list[float], lam: float, min_leaf: int):
    """idx 행들에 대해 (gain, feat, thr, miss_left) 최적 분할. g = 잔차(negative gradient)."""
    G = sum(g[i] for i in idx); H = float(len(idx))
    base = G * G / (H + lam)
    best = (0.0, -1, 0, True)
    for fi, col in enumerate(cols):
        hs = [0.0] * (NB + 1); hc = [0] * (NB + 1)
        for i in idx:
            b = col[i]
            hs[b] += g[i]; hc[b] += 1
        gm, cm = hs[NB], hc[NB]
        gl, cl = 0.0, 0
        for thr in range(NB - 1):
            gl += hs[thr]; cl += hc[thr]
            # 결측을 왼쪽으로
            for miss_left in (True, False):
                GL = gl + (gm if miss_left else 0.0); CL = cl + (cm if miss_left else 0)
                GR = G - GL; CR = int(H) - CL
                if CL < min_leaf or CR < min_leaf:
                    continue
                gain = GL * GL / (CL + lam) + GR * GR / (CR + lam) - base
                if gain > best[0]:
                    best = (gain, fi, thr, miss_left)
    return best


def _leaf(idx, g, lam, lr):
    n = Node()
    n.value = lr * (sum(g[i] for i in idx) / (len(idx) + lam)) if idx else 0.0
    return n


def fit_boost(rows: list[Row], features: list[str], *, rounds: int = 150, lr: float = 0.08,
              lam: float = 1.0, min_leaf: int = 25, depth: int = 2, subsample: float = 0.8, seed: int = 7) -> Boost | None:
    import random
    data = [(r.x, r.target) for r in rows if r.target is not None]
    if len(data) < 100:
        return None
    cuts = {f: _quantile_cuts([x.get(f) for x, _ in data if x.get(f) is not None]) for f in features}
    m = Boost(features, cuts, lr=lr, n=len(data))
    cols = [[m._bin(f, x.get(f)) for x, _ in data] for f in features]
    y = [t for _, t in data]
    m.base = sum(y) / len(y)
    pred = [m.base] * len(y)
    rng = random.Random(seed)
    all_idx = list(range(len(y)))
    for _ in range(rounds):
        g = [y[i] - pred[i] for i in all_idx]
        idx = [i for i in all_idx if rng.random() < subsample] if subsample < 1 else all_idx
        root = _grow(cols, idx, g, lam, min_leaf, lr, depth)
        m.trees.append(root)
        # update predictions for all rows
        for i in all_idx:
            n = root
            while n.left is not None:
                bi = cols[n.feat][i]
                n = n.left if (n.miss_left if bi == NB else bi <= n.thr) else n.right
            pred[i] += n.value
    return m


def _grow(cols, idx, g, lam, min_leaf, lr, depth) -> Node:
    if depth == 0 or len(idx) < 2 * min_leaf:
        return _leaf(idx, g, lam, lr)
    gain, fi, thr, ml = _best_split(cols, idx, g, lam, min_leaf)
    if fi < 0 or gain <= 1e-12:
        return _leaf(idx, g, lam, lr)
    col = cols[fi]
    left = [i for i in idx if (ml if col[i] == NB else col[i] <= thr)]
    right = [i for i in idx if not (ml if col[i] == NB else col[i] <= thr)]
    n = Node(feat=fi, thr=thr, miss_left=ml)
    n.left = _grow(cols, left, g, lam, min_leaf, lr, depth - 1)
    n.right = _grow(cols, right, g, lam, min_leaf, lr, depth - 1)
    return n


def importance(m: Boost) -> dict[str, int]:
    cnt: dict[str, int] = {}
    def walk(n: Node):
        if n.left is None:
            return
        cnt[m.features[n.feat]] = cnt.get(m.features[n.feat], 0) + 1
        walk(n.left); walk(n.right)
    for t in m.trees:
        walk(t)
    return dict(sorted(cnt.items(), key=lambda kv: -kv[1]))
