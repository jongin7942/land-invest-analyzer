"""ListingProvider — 호가 공급자 추상화 (요구사항 3).

특정 서비스에 종속시키지 않는다. 지금은 수기 입력(CSV/JSON) 하나뿐이지만,
공식 API 나 제휴 데이터가 생기면 `ListingProvider` 를 구현한 클래스를 하나 더
만들기만 하면 되고, 저장·분석·화면은 손대지 않는다.

명시적으로 만들지 않는 것 — CAPTCHA 우회, 로그인 우회, 차단 회피, 보안 우회.
비공식 크롤링이 필요하면 그 서비스의 이용약관을 먼저 확인해야 하고,
그건 코드가 대신 판단할 수 있는 일이 아니다.

수기 입력 CSV 컬럼 (헤더 필수, 순서 무관):

    필수  apt_name, trade_type(매매/전세/월세), price, exclusive_area_m2
    권장  dong, floor, top_floor, direction, features, move_in_date,
          tenant_status, agency, source_url, external_id, monthly_rent, lawd_cd

가격은 **억 단위 실수** 또는 **원 단위 정수** 둘 다 받는다. 1,000 미만이면 억으로,
그 이상이면 원으로 해석한다 — 6.2 는 6.2억, 620000000 은 6.2억이다.
"""
from __future__ import annotations

import csv
import hashlib
import json
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

from apt_engine import area, units
from apt_engine.listing import special

MANUAL_SOURCE_KEY = "manual_listing"

REQUIRED_COLUMNS = ("apt_name", "trade_type", "price", "exclusive_area_m2")
TRADE_TYPES = ("매매", "전세", "월세")

# 이 값 미만이면 억 단위로 적은 것으로 본다. 아파트 가격이 1,000원일 수는 없다.
EOK_THRESHOLD = 1000


class ListingError(ValueError):
    pass


def parse_price(raw, *, field: str = "price") -> int:
    """'6.2' → 6.2억, '620000000' → 6.2억, '62,000만' 같은 표기도 받는다."""
    if raw is None or str(raw).strip() == "":
        raise ListingError(f"{field} 가 비어 있습니다")
    text = str(raw).strip().replace(",", "").replace(" ", "")

    if text.endswith("억"):
        return int(units.from_eok(text[:-1]))
    if text.endswith("만") or text.endswith("만원"):
        return int(units.from_manwon(text.rstrip("원").rstrip("만")))
    try:
        value = float(text)
    except ValueError as e:
        raise ListingError(f"{field} 를 숫자로 읽을 수 없습니다: {raw!r}") from e
    if value <= 0:
        raise ListingError(f"{field} 는 0보다 커야 합니다: {raw!r}")
    return int(units.from_eok(text)) if value < EOK_THRESHOLD else int(units.won_round(value))


def _to_int(raw) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(float(str(raw).replace(",", "").strip()))
    except ValueError:
        return None


def fingerprint(row: dict) -> str:
    """external_id 가 없는 매물의 안정적 키.

    같은 매물을 매일 다시 넣어도 같은 키가 나와야 스냅샷이 이어진다. 그래서 가격은
    지문에 넣지 않는다 — 가격이 바뀌어도 **같은 매물의 가격 변동**으로 추적해야지
    새 매물이 되면 안 된다.
    """
    parts = [
        str(row.get("provider") or ""), str(row.get("apt_name") or ""),
        str(row.get("trade_type") or ""), f"{float(row.get('exclusive_area_m2') or 0):.2f}",
        str(row.get("dong") or ""), str(row.get("floor") or ""),
        str(row.get("agency") or ""), str(row.get("direction") or ""),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def normalize_row(raw: dict, *, provider: str, seen_on: str) -> dict:
    """수기 입력 한 줄 → listing 행. 잘못된 줄은 ListingError 로 즉시 알린다."""
    missing = [c for c in REQUIRED_COLUMNS if not str(raw.get(c) or "").strip()]
    if missing:
        raise ListingError(f"필수 항목 누락: {', '.join(missing)}")

    trade_type = str(raw["trade_type"]).strip()
    if trade_type not in TRADE_TYPES:
        raise ListingError(f"거래유형은 {'/'.join(TRADE_TYPES)} 중 하나여야 합니다: {trade_type!r}")

    try:
        exclusive = float(str(raw["exclusive_area_m2"]).replace(",", "").strip())
    except ValueError as e:
        raise ListingError(f"전용면적을 숫자로 읽을 수 없습니다: {raw['exclusive_area_m2']!r}") from e

    price = parse_price(raw["price"])
    monthly = parse_price(raw["monthly_rent"], field="monthly_rent") \
        if str(raw.get("monthly_rent") or "").strip() else 0
    if trade_type == "월세" and monthly <= 0:
        raise ListingError("월세 매물인데 monthly_rent 가 없습니다")

    floor = _to_int(raw.get("floor"))
    top_floor = _to_int(raw.get("top_floor"))
    features = (raw.get("features") or "").strip() or None
    tenant = (raw.get("tenant_status") or "").strip() or None

    flags = special.detect(features, floor=floor, top_floor=top_floor, tenant_status=tenant)

    row = {
        "provider": provider,
        "external_id": (raw.get("external_id") or "").strip() or None,
        "apt_name": str(raw["apt_name"]).strip(),
        "lawd_cd": (raw.get("lawd_cd") or "").strip() or None,
        "trade_type": trade_type,
        "price": price,
        "monthly_rent": monthly,
        "exclusive_area_m2": exclusive,
        "area_band": area.band_of(exclusive),
        "dong": (raw.get("dong") or "").strip() or None,
        "floor": floor,
        "top_floor": top_floor,
        "floor_group": special.floor_group(floor, top_floor),
        "direction": (raw.get("direction") or "").strip() or None,
        "features": features,
        "move_in_date": (raw.get("move_in_date") or "").strip() or None,
        "tenant_status": tenant,
        "agency": (raw.get("agency") or "").strip() or None,
        "source_url": (raw.get("source_url") or "").strip() or None,
        "special_flags": flags,
        "is_special": 1 if special.is_special(flags) else 0,
        "first_seen_at": seen_on,
        "last_seen_at": seen_on,
        "raw": raw,
    }
    row["listing_key"] = row["external_id"] or fingerprint(row)
    return row


class ListingProvider(ABC):
    """호가 공급자. 구현체는 정규화된 listing 행 목록을 돌려주기만 하면 된다."""

    name: str = "abstract"

    @abstractmethod
    def get_sale_listings(self, **kwargs) -> list[dict]:
        """매매 매물."""

    @abstractmethod
    def get_jeonse_listings(self, **kwargs) -> list[dict]:
        """전세 매물."""

    def get_all(self, **kwargs) -> list[dict]:
        return self.get_sale_listings(**kwargs) + self.get_jeonse_listings(**kwargs)


class ManualListingProvider(ListingProvider):
    """CSV / JSON 파일 또는 dict 목록으로 직접 넣는 공급자.

    API 가 없는 동안의 유일한 입력 경로다. 임장에서 적어 온 매물을 그대로 넣으면 된다.
    """

    name = "manual"

    def __init__(self, rows: list[dict] | None = None, *, seen_on: str | None = None,
                 provider: str | None = None):
        self.seen_on = seen_on or date.today().isoformat()
        self.provider = provider or self.name
        self._rows = [normalize_row(r, provider=self.provider, seen_on=self.seen_on)
                      for r in (rows or [])]

    # ── 입력 ──────────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, path: str | Path, *, seen_on: str | None = None,
                 provider: str | None = None) -> "ManualListingProvider":
        with open(path, newline="", encoding="utf-8-sig") as f:
            raw_rows = list(csv.DictReader(f))
        return cls._build(raw_rows, path, seen_on, provider)

    @classmethod
    def from_json(cls, path: str | Path, *, seen_on: str | None = None,
                  provider: str | None = None) -> "ManualListingProvider":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_rows = data if isinstance(data, list) else data.get("listings", [])
        return cls._build(raw_rows, path, seen_on, provider)

    @classmethod
    def _build(cls, raw_rows, path, seen_on, provider) -> "ManualListingProvider":
        inst = cls([], seen_on=seen_on, provider=provider)
        errors = []
        for i, raw in enumerate(raw_rows, start=2):   # 헤더가 1행
            try:
                inst._rows.append(normalize_row(raw, provider=inst.provider,
                                                 seen_on=inst.seen_on))
            except (ListingError, area.AreaBandError) as e:
                errors.append(f"  {i}행: {e}")
        if errors:
            raise ListingError(
                f"{path} 에서 {len(errors)}개 줄을 읽지 못했습니다:\n" + "\n".join(errors[:20]))
        return inst

    # ── 출력 ──────────────────────────────────────────────────────────

    def get_sale_listings(self, **kwargs) -> list[dict]:
        return [r for r in self._rows if r["trade_type"] == "매매"]

    def get_jeonse_listings(self, **kwargs) -> list[dict]:
        return [r for r in self._rows if r["trade_type"] == "전세"]

    def get_all(self, **kwargs) -> list[dict]:
        return list(self._rows)


TEMPLATE_CSV = """apt_name,trade_type,price,exclusive_area_m2,dong,floor,top_floor,direction,features,tenant_status,agency,source_url
동아1단지,매매,6.2,84.96,101,7,15,남향,올수리 남향 로열층,,○○공인,
동아1단지,매매,6.05,84.96,103,2,15,동향,급매 수리필요,,△△공인,
동아1단지,전세,3.7,84.96,105,9,15,남향,,세입자 승계,○○공인,
"""


def write_template(path: str | Path) -> Path:
    """수기 입력용 CSV 서식을 만들어 준다."""
    p = Path(path)
    p.write_text(TEMPLATE_CSV, encoding="utf-8")
    return p
