"""취득세·재산세·종합부동산세·양도소득세.

세율과 기준값을 코드에 하드코딩하지 않는다. tax_rule 테이블에서 as_of 기준으로
조회하고, 각 규칙은 effective_from/to + source_url + last_verified 를 가진다.

양도세와 1세대1주택 비과세는 개인 세무 판단 영역이라 결과 등급을 ESTIMATED 로
고정하고 세무사 확인 문구를 함께 낸다.

담당: PHASE 3. 아직 비어 있다.
"""
