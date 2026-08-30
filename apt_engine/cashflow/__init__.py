"""연도별 현금흐름, Initial Equity, Peak Equity, 세전·세후 IRR.

Peak Equity 와 IRR 은 반드시 같은 cash_flow 테이블에서 파생시킨다 —
따로 계산하면 어긋난다.

builder 는 매수가 P 를 인자로 받는 순수함수로 만든다. PHASE 9의 목표수익률 역산이
'P를 바꿔가며 CF 전체를 다시 만드는' 방식이라, 여기서 설계를 놓치면 나중에 전면
재작성이 필요해진다.

담당: PHASE 7. 아직 비어 있다.
"""
