"""호가(매물) 계층 — "지금 얼마를 부르고 있나".

실거래(`trade`)와 완전히 다른 것이다. 실거래는 과거에 체결된 가격이고 호가는 파는
사람의 희망가다. 요구사항 62-3이 명시적으로 금지한 대로, 둘을 섞어 계산하지 않는다.

  provider.py      ListingProvider 추상화 + ManualListingProvider(CSV/JSON)
  special.py       특수조건 매물 감지 — 최저호가가 급매/저층/수리필요면 따로 표시
  dedupe.py        같은 물건이 여러 중개업소에 올라온 것을 추정 (확정하지 않는다)
  distribution.py  호가 분포 — 최저 하나를 시장가격이라 단정하지 않는다
  gap.py           호가 vs 실거래 괴리율
  change.py        일별 스냅샷 비교 — 7/30/90일 매물·가격 변화
  pressure.py      MarketPressureScore — 기초 데이터를 먼저 계산한 뒤 점수를 만든다
"""
