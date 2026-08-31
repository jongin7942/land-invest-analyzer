"""Feature 계층 (지시서 Phase 3~6).

지시서 §74 가 순서를 못 박았다.

    데이터 → Feature → Historical Backtest → Feature usefulness → Weight → Ranking

이 패키지는 **두 번째 칸**만 담당한다. 여기서 점수를 매기지 않는다.
각 Feature 는 "이 단지의 이 성질은 얼마인가" 와 "그걸 얼마나 믿을 수 있는가" 를
따로 돌려줄 뿐이고, 그것들을 어떻게 섞을지는 백테스트가 정한다.

    base.py       Feature 계약 — 값 · 신뢰도 · 상태 · 계산근거
    momentum.py   가격 변화율·가속도 (§16·§39·§40)
    regime.py     시장 국면 7종 (§8)
    flow.py       거래량 Flow Stage · Transaction Quality (§15·§16)
    supply.py     Supply Ratio · Supply Cliff (§13)
    jeonse.py     Jeonse Lead · Downside Defense (§14)
    entry.py      Entry Price — Strong/Fair/Wait/Overpriced (§7)
    catalyst.py   Catalyst Alpha (§17)

모든 Feature 함수는 `AsOf` 를 **키워드 필수 인자**로 받고, 컷오프 가드를 통과한
커넥션으로만 데이터를 읽는다. 그래야 백테스트에서 반칙이 불가능하다.
"""
