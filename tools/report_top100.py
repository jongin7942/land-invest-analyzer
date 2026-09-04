"""TOP100 보고서 — reports/top100_latest.json 을 한 장짜리 HTML 로 만든다.

카카오톡으로 공유하려고 만든 페이지라 **휴대폰에서 먼저 읽히도록** 짰다.
좁은 화면에서는 한 단지가 한 장의 카드로, 넓은 화면에서는 표로 보인다.
검색·지역 필터·정렬은 전부 페이지 안에서 돈다(서버 없음).

점수와 신뢰도를 절대 합치지 않는다(지시서 §50). 둘 다 따로 보인다.
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "top100_latest.json"
OUT = ROOT / "reports" / "top100_latest.html"


def won_to_eok(v) -> str:
    if v is None:
        return "—"
    return f"{v / 1e8:.2f}억"


def build(data: dict) -> str:
    rows = data["rows"]
    meta = data["meta"]
    regions = sorted({r["region"] for r in rows if r.get("region")})
    payload = json.dumps(data, ensure_ascii=False)
    made = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"아파트 투자 후보 TOP{len(rows)}"

    region_opts = "".join(f'<option value="{html.escape(r)}">{html.escape(r)}</option>'
                          for r in regions)

    return f"""<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg:#F2F5F9; --surface:#FFFFFF; --ink:#141F2C; --muted:#5F6E7E; --line:#D6DEE7;
  --accent:#B25E28; --accent-ink:#FFFFFF; --teal:#2C7E79; --teal-soft:#D9EFED;
  --warn:#9A6A00; --warn-soft:#FBF0D2; --bad:#A83A2E; --bad-soft:#F8E1DD;
  --row-alt:#F7F9FC; --shadow:0 1px 2px rgba(20,31,44,.06), 0 6px 18px rgba(20,31,44,.06);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0E141B; --surface:#161E28; --ink:#E7EDF3; --muted:#95A3B2; --line:#2A3542;
    --accent:#DC8A4E; --accent-ink:#1A1208; --teal:#56B8B0; --teal-soft:#173B39;
    --warn:#E0B24D; --warn-soft:#3A2F10; --bad:#E07B6E; --bad-soft:#3E1F1B;
    --row-alt:#131B24; --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0E141B; --surface:#161E28; --ink:#E7EDF3; --muted:#95A3B2; --line:#2A3542;
  --accent:#DC8A4E; --accent-ink:#1A1208; --teal:#56B8B0; --teal-soft:#173B39;
  --warn:#E0B24D; --warn-soft:#3A2F10; --bad:#E07B6E; --bad-soft:#3E1F1B;
  --row-alt:#131B24; --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans KR","Noto Sans KR",-apple-system,"Malgun Gothic",sans-serif;
  font-size:15px; line-height:1.55; }}
.mono {{ font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace; font-variant-numeric:tabular-nums; }}
.wrap {{ max-width:1080px; margin:0 auto; padding:20px 16px 56px; }}
header h1 {{ font-size:24px; font-weight:700; margin:0 0 4px; letter-spacing:-.01em; text-wrap:balance; }}
header .spec {{ color:var(--muted); font-size:13.5px; display:flex; flex-wrap:wrap; gap:6px 14px; margin:0; padding:0; list-style:none; }}
header .spec b {{ color:var(--ink); font-weight:600; }}
.funnel {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:18px 0 14px; }}
.funnel div {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.funnel .k {{ font-size:11.5px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; }}
.funnel .v {{ font-size:22px; font-weight:600; line-height:1.2; }}
.tools {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 12px; }}
.tools input, .tools select {{ font:inherit; font-size:14px; padding:8px 10px; border:1px solid var(--line);
  border-radius:8px; background:var(--surface); color:var(--ink); min-width:0; }}
.tools input {{ flex:1 1 200px; }}
.tools select {{ flex:0 1 160px; }}
.tools .count {{ align-self:center; color:var(--muted); font-size:13px; margin-left:auto; }}
input:focus, select:focus, button:focus, th[data-sort]:focus {{ outline:2px solid var(--teal); outline-offset:2px; }}

.table-wrap {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; box-shadow:var(--shadow); overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; min-width:920px; }}
thead th {{ position:sticky; top:0; background:var(--surface); z-index:1; text-align:left; font-size:12px;
  color:var(--muted); font-weight:600; letter-spacing:.04em; padding:10px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th[data-sort] {{ cursor:pointer; user-select:none; }}
th[data-sort]:hover {{ color:var(--ink); }}
th .arrow {{ opacity:.45; font-size:10px; margin-left:3px; }}
tbody td {{ padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:middle; }}
tbody tr:nth-child(even) td {{ background:var(--row-alt); }}
tbody tr:last-child td {{ border-bottom:0; }}
td.num, th.num {{ text-align:right; }}
td.rank {{ color:var(--muted); width:44px; }}
td.name {{ font-weight:600; }}
td.name small {{ display:block; font-weight:400; color:var(--muted); font-size:12.5px; }}
.star {{ display:inline-block; background:var(--accent); color:var(--accent-ink); font-size:11px; font-weight:700;
  padding:1px 6px; border-radius:4px; margin-left:6px; vertical-align:1px; }}
.bar {{ display:inline-block; width:64px; height:6px; background:var(--line); border-radius:3px; vertical-align:middle; margin-right:6px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--teal); }}
.bar.score i {{ background:var(--ink); }}
.pill {{ display:inline-block; font-size:11.5px; padding:2px 8px; border-radius:999px; background:var(--teal-soft); color:var(--teal); font-weight:600; }}
.pill.warn {{ background:var(--warn-soft); color:var(--warn); }}
.pill.bad {{ background:var(--bad-soft); color:var(--bad); }}
.band {{ color:var(--muted); font-size:12.5px; white-space:nowrap; }}

.cards {{ display:none; }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:10px; box-shadow:var(--shadow); }}
.card .top {{ display:flex; align-items:baseline; gap:8px; }}
.card .rk {{ color:var(--muted); font-size:13px; min-width:28px; }}
.card .nm {{ font-weight:700; font-size:16px; flex:1; }}
.card .sub {{ color:var(--muted); font-size:13px; margin:2px 0 8px 36px; }}
.card .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:6px 12px; margin-left:36px; font-size:13.5px; }}
.card .grid .k {{ color:var(--muted); font-size:11.5px; display:block; }}
.card .grid .v {{ font-weight:600; }}

.notes {{ margin-top:22px; padding:14px 16px; background:var(--surface); border:1px solid var(--line); border-radius:10px; font-size:13.5px; color:var(--muted); }}
.notes h2 {{ font-size:14px; margin:0 0 8px; color:var(--ink); }}
.notes ul {{ margin:0; padding-left:18px; }}
.notes li {{ margin:4px 0; }}
footer {{ color:var(--muted); font-size:12.5px; margin-top:14px; }}

@media (max-width: 720px) {{
  .wrap {{ padding:14px 12px 40px; }}
  .funnel {{ grid-template-columns:repeat(2,1fr); }}
  .table-wrap {{ display:none; }}
  .cards {{ display:block; }}
  header h1 {{ font-size:21px; }}
}}
@media (prefers-reduced-motion: no-preference) {{
  .bar i {{ transition:width .25s ease; }}
}}
</style>

<div class="wrap">
<header>
  <h1>{html.escape(title)}</h1>
  <ul class="spec">
    <li>현금 <b>{meta['cash_eok']}억</b></li>
    <li>투자기간 <b>{meta['horizon']}년</b></li>
    <li>기준일 <b>{html.escape(meta['as_of'])}</b></li>
    <li>시장국면 <b>{html.escape(meta.get('regime') or '미상')}</b></li>
    <li>가중치 <b>{html.escape(meta.get('weights_source') or '')}</b></li>
  </ul>
</header>

<div class="funnel">
  <div><span class="k">후보</span><div class="v mono">{(meta.get('universe') or 0):,}</div></div>
  <div><span class="k">매수 가능</span><div class="v mono">{(meta.get('feasible') or 0):,}</div></div>
  <div><span class="k">TOP</span><div class="v mono">{len(rows)}</div></div>
  <div><span class="k">★ 세 리스트 모두 상위</span><div class="v mono">{sum(1 for r in rows if r.get('star'))}</div></div>
</div>

<div class="tools">
  <input id="q" type="search" placeholder="단지명·지역 검색" aria-label="검색">
  <select id="region" aria-label="지역"><option value="">모든 지역</option>{region_opts}</select>
  <span class="count" id="count"></span>
</div>

<div class="table-wrap">
<table id="t">
<thead><tr>
  <th data-sort="rank" class="num">순위<span class="arrow">▲</span></th>
  <th data-sort="name">단지</th>
  <th data-sort="region">지역</th>
  <th data-sort="approval_year" class="num">준공</th>
  <th data-sort="price" class="num">매수가</th>
  <th data-sort="equity" class="num">실투자금</th>
  <th data-sort="score" class="num">점수</th>
  <th data-sort="confidence" class="num">신뢰도</th>
  <th data-sort="kill" class="num">Kill</th>
  <th data-sort="station_m">역세권</th>
</tr></thead>
<tbody id="tb"></tbody>
</table>
</div>

<div class="cards" id="cards"></div>

<div class="notes">
  <h2>읽기 전에</h2>
  <ul>
    <li><b>점수와 신뢰도는 다른 것</b>입니다. 점수 80 / 신뢰도 40이면 "좋아 보이지만 데이터가 약한 후보"입니다. 신뢰도 50 미만은 표본이 얇습니다.</li>
    <li>가중치는 <b>임시(HEURISTIC)</b>입니다. 후보를 좁히는 용도이고, 백테스트가 학습값으로 바꿉니다. 이 순위로 바로 투자 판단을 하지 마세요.</li>
    <li>실투자금은 매수가에서 대출·전세 승계를 뺀 자기자본입니다. 취득세·부대비용 포함.</li>
    <li>역세권은 <b>단지 중심점</b>에서 다니는 역까지 직선거리입니다. 대단지는 경계 기준으로 더 가까울 수 있습니다. 착공·계획 중인 역은 세지 않습니다 — 개통 사례 117건에서 개통 효과가 측정되지 않았습니다.</li>
    <li>모델은 단지 단위 중앙값을 봅니다. 층·향·동 같은 개별 매물 조건은 반영되지 않습니다.</li>
    <li>★ 는 절대수익·위험조정·비대칭 세 리스트에 모두 상위로 든 단지입니다.</li>
  </ul>
</div>
<footer>생성 {made} · land-invest-analyzer</footer>
</div>

<script id="data" type="application/json">{payload}</script>
<script>
(function(){{
  const data = JSON.parse(document.getElementById('data').textContent);
  const rows = data.rows;
  const tb = document.getElementById('tb'), cards = document.getElementById('cards');
  const q = document.getElementById('q'), region = document.getElementById('region'), count = document.getElementById('count');
  let sortKey = 'rank', sortDir = 1;

  const eok = v => v == null ? '—' : (v/1e8).toFixed(2) + '억';
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
  const confPill = c => c >= 50 ? '' : (c >= 35 ? '<span class="pill warn">약함</span>' : '<span class="pill bad">매우 약함</span>');
  const killPill = k => k <= 0 ? '<span class="pill">0.00</span>' : (k < 0.5 ? '<span class="pill warn">'+k.toFixed(2)+'</span>' : '<span class="pill bad">'+k.toFixed(2)+'</span>');
  const station = r => r.station ? esc(r.station) + ' ' + Math.round(r.station_m).toLocaleString() + 'm' : '2km 안에 역 없음';

  function visible(){{
    const s = q.value.trim().toLowerCase(), g = region.value;
    let v = rows.filter(r => (!g || r.region === g) && (!s || (r.name + ' ' + r.region).toLowerCase().includes(s)));
    v.sort((a,b) => {{
      const x = a[sortKey], y = b[sortKey];
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === 'string' ? x.localeCompare(y, 'ko') : x - y) * sortDir;
    }});
    return v;
  }}

  function render(){{
    const v = visible();
    count.textContent = v.length + ' / ' + rows.length + '개';
    tb.innerHTML = v.map(r => `
      <tr>
        <td class="rank mono num">${{r.rank}}</td>
        <td class="name">${{esc(r.name)}}${{r.star ? '<span class="star">★</span>' : ''}}<small>${{r.households ? r.households.toLocaleString()+'세대' : ''}} · ${{r.area_band}}㎡</small></td>
        <td>${{esc(r.region)}}</td>
        <td class="mono num">${{r.approval_year ?? '—'}}</td>
        <td class="mono num">${{eok(r.price)}}</td>
        <td class="mono num">${{eok(r.equity)}}</td>
        <td class="mono num"><span class="bar score"><i style="width:${{Math.round(r.score)}}%"></i></span>${{Math.round(r.score)}}</td>
        <td class="mono num"><span class="bar"><i style="width:${{Math.round(r.confidence)}}%"></i></span>${{Math.round(r.confidence)}} ${{confPill(r.confidence)}}</td>
        <td class="num">${{killPill(r.kill)}}</td>
        <td class="band">${{station(r)}}</td>
      </tr>`).join('');
    cards.innerHTML = v.map(r => `
      <div class="card">
        <div class="top"><span class="rk mono">${{r.rank}}</span><span class="nm">${{esc(r.name)}}${{r.star ? '<span class="star">★</span>' : ''}}</span></div>
        <div class="sub">${{esc(r.region)}} · ${{r.approval_year ?? '—'}}년 · ${{r.households ? r.households.toLocaleString()+'세대' : ''}} · ${{r.area_band}}㎡</div>
        <div class="grid">
          <div><span class="k">매수가</span><span class="v mono">${{eok(r.price)}}</span></div>
          <div><span class="k">실투자금</span><span class="v mono">${{eok(r.equity)}}</span></div>
          <div><span class="k">점수</span><span class="v mono">${{Math.round(r.score)}}</span></div>
          <div><span class="k">신뢰도</span><span class="v mono">${{Math.round(r.confidence)}} ${{confPill(r.confidence)}}</span></div>
          <div><span class="k">Kill</span><span class="v">${{killPill(r.kill)}}</span></div>
          <div><span class="k">역세권</span><span class="v band">${{station(r)}}</span></div>
        </div>
      </div>`).join('');
  }}

  document.querySelectorAll('th[data-sort]').forEach(th => {{
    th.tabIndex = 0;
    const go = () => {{
      const k = th.dataset.sort;
      if (sortKey === k) sortDir = -sortDir; else {{ sortKey = k; sortDir = (k === 'name' || k === 'region' || k === 'rank' || k === 'station_m') ? 1 : -1; }}
      document.querySelectorAll('th .arrow').forEach(a => a.remove());
      th.insertAdjacentHTML('beforeend', '<span class="arrow">' + (sortDir > 0 ? '▲' : '▼') + '</span>');
      render();
    }};
    th.addEventListener('click', go);
    th.addEventListener('keydown', e => {{ if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); go(); }} }});
  }});
  q.addEventListener('input', render);
  region.addEventListener('change', render);
  render();
}})();
</script>
"""


def main() -> int:
    if not SRC.exists():
        print(f"{SRC} 가 없습니다 — tools/dump_top100.py 를 먼저 돌리세요.", file=sys.stderr)
        return 1
    data = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.write_text(build(data), encoding="utf-8")
    print(f"보고서 → {OUT}  ({len(data['rows'])}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
