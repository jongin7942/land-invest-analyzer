"""실거래 단지명 ↔ K-apt 단지코드 매칭.

이 프로젝트 최대의 숨은 난제다. 실거래가 API 는 단지명을 **문자열로만** 주고,
그 문자열이 K-apt 의 단지명과 일치한다는 보장이 전혀 없다:

    'e편한세상' / '이편한세상' / 'E-편한세상'
    '○○마을 1단지' / '○○1단지' / '○○아파트(1단지)'
    '래미안' 이라는 이름의 서로 다른 단지가 한 구에 여럿

토지 프로그램의 `parse_sgg_umd()` 는 이런 매칭을 문자열 추정으로 하고, **실패해도
조용히 상위 기준선으로 폴백**해서 에러 없이 정확도만 떨어졌다. 같은 실수를 반복하지
않으려고 여기서는:

  * 단일 문자열이 아니라 **다중키**(시군구 + 정규화 이름 + 법정동 + 건축년도)로 붙이고,
  * 결과에 **신뢰도**(EXACT/STRONG/WEAK/NONE)와 **근거 문장**을 남기고,
  * 후보가 둘 이상인데 구별할 근거가 없으면 **붙이지 않는다**(NONE).
    억지로 붙인 매칭은 안 붙인 것보다 나쁘다 — 틀린 가격이 조용히 섞여든다.

미매칭 건은 버리지 않는다. `trade.complex_id` 를 NULL 로 두고 저장한 뒤,
`cli report unmatched` 로 빈도순으로 보여준다. 규칙을 고치면 다시 붙이면 된다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# 로마자로 표기되는 브랜드명 → 한글. 실거래가와 K-apt 가 서로 다르게 적는 대표 사례.
BRAND_ALIASES = (
    ("e편한세상", "이편한세상"),
    ("e-편한세상", "이편한세상"),
    ("이-편한세상", "이편한세상"),
    ("xi", "자이"),
    ("ipark", "아이파크"),
    ("i-park", "아이파크"),
    ("the#", "더샵"),
    ("the sharp", "더샵"),
    ("prugio", "푸르지오"),
    ("skview", "에스케이뷰"),
    ("sk view", "에스케이뷰"),
    ("lh", "엘에이치"),
    ("we've", "위브"),
    ("weve", "위브"),
    ("castle", "캐슬"),
    ("hillstate", "힐스테이트"),
    ("hill state", "힐스테이트"),
    # 브랜드가 아니라 **음차 조각**. K-apt 는 한글로, 실거래는 로마자로 적는다.
    # 긴 것부터 치환하므로 'skview' → 'sky' → 'sk' 순으로 안전하게 걸린다.
    ("sky", "스카이"),
    ("view", "뷰"),
    ("sk", "에스케이"),
    ("leaders", "리더스"),
    ("park", "파크"),
    ("city", "시티"),
    ("town", "타운"),
    ("blue", "블루"),
    ("lake", "레이크"),
)

# 이름 끝에 붙어 의미를 바꾸지 않는 꼬리표. 붙어 있어도 없어도 같은 단지다.
DROP_SUFFIXES = ("아파트", "apt", "APT", "@")

_PUNCT = re.compile(r"[\s()\[\]{}·.,\-_/'\"’‘“”]+")
_ORDINAL = re.compile(r"제\s*(\d+)\s*(단지|차)")


def normalize(name: str | None) -> str:
    """매칭용 정규화 이름.

    지우는 것: 공백·괄호·기호, '아파트' 꼬리표, 로마자 브랜드 표기 차이
    남기는 것: **숫자와 단지/차 표기** — '1단지'와 '2단지'는 다른 단지다.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip().lower()

    # '제1단지' → '1단지'
    s = _ORDINAL.sub(r"\1\2", s)

    # 로마자 브랜드 → 한글 (긴 것부터 치환해야 'sk view' 가 'sk' 로 먼저 안 잡힌다)
    for src, dst in sorted(BRAND_ALIASES, key=lambda kv: -len(kv[0])):
        s = s.replace(src.lower(), dst)

    s = _PUNCT.sub("", s)

    for suffix in DROP_SUFFIXES:
        low = suffix.lower()
        while s.endswith(low) and len(s) > len(low):
            s = s[: -len(low)]

    return s


# ── 표기 변형 ────────────────────────────────────────────────────────────
# 실거래가와 K-apt 는 같은 단지를 이렇게 다르게 적는다(2026-08-31 인천 실측):
#
#   실거래 '부개주공3'   ↔  K-apt '부개주공3단지아파트'    끝의 '단지' 유무
#   실거래 '주공5'(부개동) ↔  K-apt '부개 주공5단지'        법정동 접두어 유무
#   실거래 '동아(1차)'    ↔  K-apt '부평 동아1단지'         '차' ↔ '단지'
#   실거래 '뉴서울'(부개동)↔  K-apt '부개 뉴서울'           법정동 접두어
#
# 그래서 양쪽 이름에서 **같은 규칙으로** 변형 집합을 만들어 교집합을 본다.
# 유사도(SequenceMatcher)로 뭉개지 않는 이유: '뉴서울'과 '뉴서울2차'는 유사도가
# 높지만 서로 다른 단지다. 변형은 규칙이 명시적이라 무엇이 왜 붙었는지 말할 수 있다.
_EMD_TAIL = re.compile(r"\d*(?:동|읍|면|리|가)$")
_SERIES = re.compile(r"(\d+)\s*(?:차|단지)")
_TRAIL_NUM = re.compile(r"\d$")


_SGG_TAIL = re.compile(r"(?:특별시|광역시|특별자치시|특별자치도|시|군|구|도)$")
_LEAD_SERIES = re.compile(r"^(\d+)(?:차|단지)(.+)$")


def sgg_stems(sgg_name: str | None) -> list[str]:
    """'인천 부평구' → ['인천', '부평'].

    K-apt 는 단지명 앞에 법정동이 아니라 시군구나 지역 통칭을 붙이기도 한다
    ('현대1차'(산곡동) ↔ '부평현대1단지'). 그래서 시군구 어간도 접두어 후보로 둔다.
    """
    if not sgg_name:
        return []
    out = []
    for part in str(sgg_name).split():
        stem = _SGG_TAIL.sub("", normalize(part))
        if len(stem) >= 2:
            out.append(stem)
    return out


def emd_stem(emd_name: str | None) -> str:
    """'부개동' → '부개'. 접두어로 쓰이는 법정동 어간."""
    if not emd_name:
        return ""
    return _EMD_TAIL.sub("", normalize(emd_name))


def variants(name_norm: str, emd_name: str | None = None,
             sgg_name: str | None = None) -> frozenset[str]:
    """한 이름의 동치 표기 집합.

    법정동 어간은 **그 행 자신의 법정동**으로만 떼어낸다. 그래서 '갈산주공1단지'와
    '부개주공1단지'가 둘 다 '주공1단지'로 줄더라도, 뒤에서 법정동으로 다시 가른다.
    """
    if not name_norm:
        return frozenset()
    out = {name_norm, _SERIES.sub(r"\1단지", name_norm)}

    # 이름 가운데 낀 '아파트' 는 의미가 없다: '동아아파트2단지' → '동아2단지'
    for v in list(out):
        stripped = v.replace("아파트", "")
        if stripped and stripped != v:
            out.add(stripped)
            out.add(_SERIES.sub(r"\1단지", stripped))

    for stem in [emd_stem(emd_name), *sgg_stems(sgg_name)]:
        if len(stem) < 2:
            continue
        for v in list(out):
            if v.startswith(stem) and len(v) > len(stem):
                out.add(v[len(stem):])

    # 앞에 붙은 차수를 뒤로 돌린다: '2차우성' → '우성2차'
    for v in list(out):
        m = _LEAD_SERIES.match(v)
        if m:
            out.add(m.group(2) + m.group(1) + "단지")

    # 숫자로 끝나면 '단지'가 생략된 표기로 본다: '주공3' → '주공3단지'
    for v in list(out):
        if _TRAIL_NUM.search(v):
            out.add(v + "단지")
    return frozenset(out)


def similarity(a: str, b: str) -> float:
    """0~1. 정규화된 이름끼리 비교한다."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class Candidate:
    """매칭 후보 단지 — DB 행에서 필요한 것만 추린 값."""
    complex_id: int
    name: str
    name_norm: str
    emd_name: str | None = None
    approval_year: int | None = None


@dataclass(frozen=True)
class MatchResult:
    complex_id: int | None
    confidence: str          # EXACT / STRONG / WEAK / NONE
    reason: str

    @property
    def matched(self) -> bool:
        return self.complex_id is not None


# 이 값 아래로 떨어지면 아예 후보로 보지 않는다. 억지 매칭 방지.
FUZZY_FLOOR = 0.85

# 건축년도와 사용승인연도는 1~2년 어긋나는 게 정상이다(준공과 승인 시차).
YEAR_TOLERANCE = 2


def _year_ok(build_year: int | None, approval_year: int | None) -> bool:
    if build_year is None or approval_year is None:
        return False
    return abs(build_year - approval_year) <= YEAR_TOLERANCE


def match(apt_name: str, candidates: list[Candidate], *,
          emd_name: str | None = None, build_year: int | None = None,
          sgg_name: str | None = None) -> MatchResult:
    """실거래 한 건의 단지명을 후보 목록에 붙인다.

    candidates 는 **같은 시군구의 단지들**이어야 한다(호출부가 좁혀서 넘긴다).
    시군구가 다르면 같은 이름이라도 다른 단지다.
    """
    target = normalize(apt_name)
    if not target:
        return MatchResult(None, "NONE", "단지명이 비어 있음")
    if not candidates:
        return MatchResult(None, "NONE", "같은 시군구에 등록된 단지가 없음(K-apt 미수집)")

    exact = [c for c in candidates if c.name_norm == target]

    # ── 1. 정규화 이름이 정확히 하나만 일치 ────────────────────────────
    if len(exact) == 1:
        return MatchResult(exact[0].complex_id, "EXACT",
                           f"정규화 단지명 완전일치 '{target}'")

    # ── 2. 동명 단지가 여럿 — 법정동·건축년도로 가른다 ─────────────────
    if len(exact) > 1:
        narrowed = exact
        used = []
        if emd_name:
            by_emd = [c for c in narrowed if c.emd_name == emd_name]
            if by_emd:
                narrowed = by_emd
                used.append(f"법정동 '{emd_name}'")
        if len(narrowed) > 1 and build_year is not None:
            by_year = [c for c in narrowed if _year_ok(build_year, c.approval_year)]
            if by_year:
                narrowed = by_year
                used.append(f"건축년도 {build_year}±{YEAR_TOLERANCE}")
        if len(narrowed) == 1:
            return MatchResult(narrowed[0].complex_id, "STRONG",
                               f"동명 단지 {len(exact)}개를 {' + '.join(used)}로 특정")
        # 끝까지 못 가림 — 억지로 고르지 않는다.
        names = ", ".join(sorted({c.name for c in exact}))[:120]
        return MatchResult(
            None, "NONE",
            f"동명 단지 {len(exact)}개를 구별할 근거가 없음 ({names}). "
            f"근거 없이 합치지 않는다")

    # ── 3. 표기 변형으로 일치 ──────────────────────────────────────────
    # '부개주공3' ↔ '부개주공3단지' 같은 표기 차이. 규칙이 명시적이라 근거를 쓸 수 있다.
    tvars = variants(target, emd_name, sgg_name)
    vmatch = [c for c in candidates
              if variants(c.name_norm, c.emd_name, sgg_name) & tvars]
    if vmatch:
        narrowed, used = vmatch, []
        if len(narrowed) > 1 and emd_name:
            by_emd = [c for c in narrowed if c.emd_name == emd_name]
            if by_emd:
                narrowed, _ = by_emd, used.append(f"법정동 '{emd_name}'")
        if len(narrowed) > 1 and build_year is not None:
            by_year = [c for c in narrowed if _year_ok(build_year, c.approval_year)]
            if by_year:
                narrowed, _ = by_year, used.append(f"건축년도 {build_year}±{YEAR_TOLERANCE}")

        if len(narrowed) == 1:
            best = narrowed[0]
            # 표기 변형만으로는 STRONG 을 주지 않는다. 법정동이나 건축년도가
            # 한 번 더 받쳐줘야 한다 — 안 그러면 '동아1단지'와 '동아1차'가 서로
            # 다른 단지인 경우를 걸러낼 방법이 없다.
            corroborated = (emd_name is not None and best.emd_name == emd_name)                 or _year_ok(build_year, best.approval_year)
            detail = f"표기 변형 일치 '{target}' → '{best.name}'"
            if used:
                detail += f" ({' + '.join(used)}로 특정)"
            if corroborated:
                return MatchResult(best.complex_id, "STRONG",
                                   detail + " + 법정동/건축년도 확인")
            return MatchResult(best.complex_id, "WEAK", detail + " (교차확인 없음, 검증 권장)")

        names = ", ".join(sorted({c.name for c in narrowed}))[:120]
        return MatchResult(
            None, "NONE",
            f"표기 변형 후보 {len(narrowed)}개를 구별할 근거가 없음 ({names})")

    # ── 4. 완전일치·변형일치 없음 — 유사도로 후보를 찾는다 ─────────────
    scored = sorted(
        ((similarity(target, c.name_norm), c) for c in candidates),
        key=lambda sc: sc[0], reverse=True,
    )
    best_score, best = scored[0]
    if best_score < FUZZY_FLOOR:
        return MatchResult(None, "NONE",
                           f"유사한 단지 없음 (최고 유사도 {best_score:.2f} < {FUZZY_FLOOR})")

    # 1등과 2등이 비등하면 특정된 게 아니다.
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < 0.03:
        return MatchResult(None, "NONE",
                           f"유사 후보가 비등함 ({best_score:.2f} vs {runner_up:.2f})")

    # 건축년도까지 맞으면 STRONG, 아니면 WEAK 로만 둔다.
    if _year_ok(build_year, best.approval_year):
        return MatchResult(best.complex_id, "STRONG",
                           f"유사도 {best_score:.2f} + 건축년도 일치 → '{best.name}'")
    return MatchResult(best.complex_id, "WEAK",
                       f"유사도 {best_score:.2f} 만으로 추정 → '{best.name}' (검증 권장)")
