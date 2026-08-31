"""Walk-forward 백테스트 (지시서 §55·§56·§57·§71·§72·§74).

    데이터 → Feature → **Historical Backtest** → Feature usefulness
    → Weight → Ranking                                          (§74)

이 패키지가 세 번째와 네 번째 칸이다. 여기가 돌기 전까지 가중치는
`HEURISTIC` 을 벗어날 수 없고, 랭킹 결과에 그 사실이 표시된다.

구성:
    windows.py     시점 창 생성 + Train/Validation/OOT 시간 분할 (§72)
    outcome.py     정답 계산 — 미래 수익률·MDD·회복·Winner 분류 (§36·§41)
    kpi.py         KPI 14종 (§57)
    usefulness.py  Feature 유용성 → 가중치 (§71·§74)
    leakage.py     누출 감사 — 누출이 발견되면 그 실행은 무효 (§55·§69)
    synthetic.py   합성 시장 — 하네스 자체를 검증하기 위한 것
    runner.py      전체 구동
"""
