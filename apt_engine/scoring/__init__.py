"""점수 계층 (Phase 7).

지시서 §49: 하나의 거대한 scoring formula 에 모든 것을 넣지 않는다.
독립 모델 9종을 각각 계산하고, 모델 간 Consensus 를 별도로 본다.

    normalize.py  후보 집단 안에서의 상대 위치 (절대 임계값을 쓰지 않는다)
    models.py     독립 모델 9종
    weights.py    가중치 — 국면별로 다르고, 출처(HEURISTIC/BACKTESTED)가 기록된다
    kill.py       Kill Score (§45)
    thesis.py     Thesis Survival (§23)
    consensus.py  모델 합성 · Score 와 Confidence 분리 (§50)

**여기서 정한 가중치는 전부 임시다.** `weights_source='HEURISTIC'` 으로 기록되고,
백테스트가 돌면 `BACKTESTED` 로 교체된다. 지시서 §74 가 허용한 범위
("초기 heuristic score 는 candidate discovery 에만") 를 넘지 않기 위해서다.
"""
