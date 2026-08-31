"""랭킹 계층 (Phase 7).

지시서 §1 이 순서를 고정했다.

    수도권 전체 → Blind Candidate Generation → TOP100 → Deep Dive TOP30
    → Final TOP10 → **그 다음에야** 사용자 관심단지 표시

    pipeline.py  위 순서를 그대로 구현한다
    lists.py     Absolute / Risk-adjusted / Asymmetric 세 리스트 (§48)
    explain.py   왜 이 아파트인가 · 왜 다른 아파트가 아닌가 (§75·§76)
"""
