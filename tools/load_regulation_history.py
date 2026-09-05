"""rules/regulation_zone_history.csv → DB regulation_zone (전체 교체).

시군구 표기 → lawd_cd 매핑: region 테이블(name = '경기 성남 분당구' 등).
  '전체'            → 해당 시도의 모든 시군구
  '성남시'          → 이름에 '성남' 이 들어가는 모든 코드(구 단위 코드 포함)
  '수원시 영통구'   → '수원 영통구' 정확 일치
emd_scope: ALL / INCLUDE(emd_list 만 해당) / EXCLUDE(emd_list 제외). emd_list 는 '|' 구분, 괄호 안 목록도 허용.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402

SRC = ROOT / "rules" / "regulation_zone_history.csv"
TAG = "HISTORY_v1"


def _norm(n: str) -> str:
    parts = n.split(" ")
    if parts and parts[0] in ("서울", "경기", "인천"):
        parts = parts[1:]
    return " ".join(parts)


def codes_for(reg: list[tuple[str, str, str]], sido: str, sgg: str) -> list[str]:
    names = [(c, _norm(n)) for c, s, n in reg if s == sido]
    if sgg == "전체":
        return [c for c, _ in names]
    exact = [c for c, n in names if n == sgg]
    if exact:
        return exact
    return [c for c, n in names if n.startswith(sgg)]      # '성남시' → 성남시 수정구/중원구/분당구


def emd_items(s: str) -> list[str]:
    if not s:
        return []
    m = re.search(r"\((.*)\)", s)
    body = m.group(1) if m else s
    return [x.strip() for x in body.split("|") if x.strip()]


def main() -> int:
    with get_conn() as conn:
        reg = [(r["lawd_cd"], r["sido"], r["name"]) for r in conn.execute("SELECT lawd_cd, sido, name FROM region")]
        rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
        out, miss = [], []
        for r in rows:
            codes = codes_for(reg, r["sido"], r["sigungu"])
            if not codes:
                miss.append((r["sido"], r["sigungu"])); continue
            emds = emd_items(r["emd_list"])
            for c in codes:
                if r["emd_scope"] == "INCLUDE":
                    for e in emds:
                        out.append((c, e, r["zone_type"], r["effective_from"], r["effective_to"] or None, r["source_name"], r["source_url"], r["confidence"], r["note"]))
                elif r["emd_scope"] == "EXCLUDE":
                    out.append((c, None, r["zone_type"], r["effective_from"], r["effective_to"] or None, r["source_name"], r["source_url"], r["confidence"],
                                (r["note"] or "") + " | EXCLUDE:" + "|".join(emds)))
                else:
                    out.append((c, None, r["zone_type"], r["effective_from"], r["effective_to"] or None, r["source_name"], r["source_url"], r["confidence"], r["note"]))
        # 확장본(코드 단위, 4종 모두) → rules/regulation_zone_expanded.csv — 패널이 DB 대신 이 파일을 읽는다
        with (ROOT / "rules" / "regulation_zone_expanded.csv").open("w", encoding="utf-8", newline="") as fo:
            w = csv.writer(fo); w.writerow(["lawd_cd", "emd_name", "zone_type", "effective_from", "effective_to", "confidence", "exclude", "source_url"])
            for c, e, z, f, t, src, url, conf, note in out:
                excl = note.split("EXCLUDE:", 1)[1] if "EXCLUDE:" in (note or "") else ""
                w.writerow([c, e or "", z, f, t or "", conf, excl, url])
        out_db = [o for o in out if o[2] in ("조정대상지역", "투기과열지구", "투기지역")]   # DB CHECK 제약: 분양가상한제 는 CSV 에만
        conn.execute("DELETE FROM regulation_zone")
        conn.executemany(
            "INSERT INTO regulation_zone(lawd_cd, emd_name, zone_type, effective_from, effective_to, source_name, source_url, last_verified, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(c, e, z, f, t, f"{TAG}|{src}|{conf}", url, "2026-09-05", note) for c, e, z, f, t, src, url, conf, note in out_db])
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM regulation_zone").fetchone()[0]
        by = conn.execute("SELECT zone_type, COUNT(*) FROM regulation_zone GROUP BY zone_type").fetchall()
    print(f"적재 {n}행 · {[tuple(b) for b in by]} · 매핑 실패 {miss}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
