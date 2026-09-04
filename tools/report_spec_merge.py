"""MASTER_SPEC 병합 결과를 한 장의 HTML 로 묶는다 (Artifact / 카톡 링크용).

    .venv/Scripts/python.exe tools/report_spec_merge.py  → reports/spec_merge_2026-09-04.html
"""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "spec" / "MASTER_SPEC.md").read_text(encoding="utf-8")
LOG = (ROOT / "spec" / "RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1.md").read_text(encoding="utf-8")
OUT = ROOT / "reports" / "spec_merge_2026-09-04.html"

TEMPLATE = """<title>MASTER SPEC 정비사업 옵션 병합</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@500;700&family=Noto+Sans+KR:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root{--bg:#f7f5f0;--paper:#fffdf9;--ink:#1e2a1f;--muted:#5f6b60;--line:#d9d3c5;--accent:#1f6f50;--accent-soft:#e3efe7;--warn:#9a5b10;--warn-soft:#f6ead6;--code:#eef0ea;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#151a16;--paper:#1c221d;--ink:#e6e8e2;--muted:#a3ab9f;--line:#333b34;--accent:#7cc7a2;--accent-soft:#213a2d;--warn:#e0a85a;--warn-soft:#3a2d18;--code:#242b25;}}
:root[data-theme="dark"]{--bg:#151a16;--paper:#1c221d;--ink:#e6e8e2;--muted:#a3ab9f;--line:#333b34;--accent:#7cc7a2;--accent-soft:#213a2d;--warn:#e0a85a;--warn-soft:#3a2d18;--code:#242b25;}
body{background:var(--bg);color:var(--ink);font-family:"Noto Sans KR",system-ui,sans-serif;line-height:1.6;}
.wrap{max-width:900px;margin:0 auto;padding:28px 18px 80px;}
header{border-bottom:2px solid var(--accent);padding-bottom:14px;margin-bottom:22px;}
header h1{font-family:"Noto Serif KR",serif;font-size:1.7rem;margin:0 0 6px;text-wrap:balance;}
header p{margin:0;color:var(--muted);font-size:.95rem;}
.eyebrow{font-size:.75rem;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:700;}
nav{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 26px;}
nav a{font-size:.85rem;padding:5px 10px;border:1px solid var(--line);border-radius:999px;color:var(--ink);text-decoration:none;background:var(--paper);}
nav a:hover,nav a:focus-visible{border-color:var(--accent);outline:none;}
section{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:20px 22px;margin-bottom:18px;}
section h2{font-family:"Noto Serif KR",serif;font-size:1.25rem;margin:0 0 12px;}
.todo{background:var(--warn-soft);border-left:4px solid var(--warn);padding:12px 16px;border-radius:6px;}
.todo strong{color:var(--warn);}
details summary{cursor:pointer;font-weight:700;color:var(--accent);}
.md h1{font-family:"Noto Serif KR",serif;font-size:1.5rem;margin-top:0;}
.md h2{font-size:1.15rem;border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:28px;}
.md h3{font-size:1rem;margin-top:20px;}
.md h4{font-size:.95rem;margin-top:16px;color:var(--muted);}
.md table{border-collapse:collapse;width:100%;font-size:.88rem;margin:10px 0;font-variant-numeric:tabular-nums;}
.md th,.md td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top;}
.md th{background:var(--accent-soft);}
.md .tbl{overflow-x:auto;}
.md pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto;font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.82rem;line-height:1.5;}
.md code{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.86em;background:var(--code);padding:1px 4px;border-radius:3px;}
.md pre code{background:none;padding:0;}
.md blockquote{border-left:3px solid var(--accent);margin:10px 0;padding:4px 14px;color:var(--muted);}
.md li{margin:3px 0;}
.md hr{border:0;border-top:1px solid var(--line);margin:26px 0;}
.meta{font-size:.85rem;color:var(--muted);}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">수도권 아파트 투자 엔진 · 기준문서</div>
  <h1>MASTER SPEC — 정비사업 Option Value Engine v0.1 병합</h1>
  <p>2026-09-04 · 병합 전 원본 git b22a2e0 → 병합본 v2026-09-04a · 전체 테스트 102 통과 · Stage Registry 2,404건</p>
</header>
<nav>
  <a href="#todo">종인님이 하실 일</a><a href="#changed">변경 섹션</a><a href="#vars">새 변수·공식</a>
  <a href="#conflict">수정·제외한 DELTA</a><a href="#nv">NEEDS_VERIFICATION</a><a href="#dc">Double Counting</a>
  <a href="#code">코드 체크리스트</a><a href="#spec">MASTER_SPEC 전문</a><a href="#log">연구로그 전문</a>
</nav>

<section id="todo"><h2>종인님이 하실 일</h2>
<div class="todo"><strong>지금은 없습니다.</strong> 병합본은 <code>land-invest-analyzer/spec/MASTER_SPEC.md</code>에 있고, 이 페이지가 그 사본입니다. ChatGPT Work 쪽 MASTER_SPEC.md를 이 병합본으로 교체해 주시면 양쪽 기준이 같아집니다(아래 "MASTER_SPEC 전문"을 복사하거나 로컬 파일을 올리시면 됩니다).</div>
<p class="meta">앞서 드린 세 가지(세법 규칙 32건 원문 확인 · 동아 보유 조건값 · 나머지 spec 파일 5개)는 그대로 남아 있습니다.</p>
</section>

<section id="changed"><h2>2. 변경된 섹션</h2><div class="md" data-src="changed"></div></section>
<section id="vars"><h2>3. 새로 추가된 변수·공식</h2><div class="md" data-src="vars"></div></section>
<section id="conflict"><h2>4. 기존 규칙과 충돌하여 수정·제외한 DELTA 항목</h2><div class="md" data-src="conflict"></div></section>
<section id="nv"><h2>5. NEEDS_VERIFICATION 으로 남긴 항목</h2><div class="md" data-src="nv"></div></section>
<section id="dc"><h2>6. Double Counting 검사 결과</h2><div class="md" data-src="dc"></div></section>
<section id="code"><h2>7. 구현 시 수정해야 할 코드/DB 체크리스트</h2><div class="md" data-src="code"></div></section>
<section id="spec"><h2>1. 병합 완료된 MASTER_SPEC.md 전체</h2>
<details open><summary>펼치기/접기</summary><div class="md" data-src="spec"></div></details></section>
<section id="log"><h2>연구로그 RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1.md 전체</h2>
<details><summary>펼치기/접기</summary><div class="md" data-src="log"></div></details></section>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script>
const SRC = __SRC__;
marked.setOptions({gfm:true, breaks:false});
for (const el of document.querySelectorAll('.md[data-src]')) {
  el.innerHTML = marked.parse(SRC[el.dataset.src] || '');
  for (const t of el.querySelectorAll('table')) { const w=document.createElement('div'); w.className='tbl'; t.replaceWith(w); w.appendChild(t); }
}
</script>
"""


def section(md: str, start: str, end: str | None) -> str:
    i = md.index(start)
    j = md.index(end, i + 1) if end else len(md)
    return md[i:j].strip()


def main() -> int:
    src = {
        "spec": SPEC,
        "log": LOG,
        "changed": section(LOG, "## 1. 병합된 규칙", "## 3. 새 변수"),
        "vars": section(LOG, "## 3. 새 변수", "## 4. 기존 규칙"),
        "conflict": section(LOG, "## 4. 기존 규칙", "## 5. NEEDS_VERIFICATION"),
        "nv": section(LOG, "## 5. NEEDS_VERIFICATION", "## 6. Double Counting"),
        "dc": section(LOG, "## 6. Double Counting", "## 7. 구현 체크리스트"),
        "code": section(LOG, "## 7. 구현 체크리스트", "## 8. 테스트 결과"),
    }
    page = TEMPLATE.replace("__SRC__", json.dumps(src, ensure_ascii=False).replace("</", "<\\/"))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(OUT, f"{OUT.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
