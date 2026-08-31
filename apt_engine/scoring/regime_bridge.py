"""국면 이름을 가중치 계층으로 넘기는 얇은 다리.

`features.regime` 과 `scoring.weights` 가 서로를 import 하면 순환이 된다.
국면 이름 문자열만 오가면 되므로 여기서 목록이 일치하는지만 확인한다.
"""
from apt_engine.features.regime import REGIMES
from apt_engine.scoring.weights import REGIME_ADJUST

# 국면을 늘렸는데 가중치를 안 만들면 그 국면에서 기본 가중치가 조용히 쓰인다.
UNCOVERED = tuple(r for r in REGIMES if r not in REGIME_ADJUST)
