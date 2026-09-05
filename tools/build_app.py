"""종인님용 '아파트 엔진 앱' — 휴대폰에서 투자금·지역·면적을 골라 TW 순위를 보는 단일 HTML (Artifact 링크용).

서버 없이 동작한다: 투자금 1·2·3·5억 × 수도권 전역(1,000세대 이상, 예측 있는 단지) TW 결과를 미리 계산해 JSON 으로 심는다.
입력: reports/tw_all_{1,2,3,5}eok.csv, reports/tw_stability_all_{...}.json, rules/exit_price_2026.csv,
      rules/relative_followers.csv, rules/option_stage_registry.csv, DB region 이름
출력: reports/apt_app.html
    .venv/Scripts/python.exe tools/build_app.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402

R = ROOT / "reports"
RULES = ROOT / "rules"
CASHES = ["1", "2", "3", "5"]


def read_csv(p: Path):
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fnum(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def main() -> int:
    with get_conn() as conn:
        region = {r["lawd_cd"]: r["name"] for r in conn.execute("SELECT lawd_cd, name FROM region")}
        hh = {int(r["id"]): r["apt_households"] for r in conn.execute("SELECT id, apt_households FROM complex WHERE apt_households IS NOT NULL")}
        yr = {int(r["id"]): r["approval_year"] for r in conn.execute("SELECT id, approval_year FROM complex WHERE approval_year IS NOT NULL")}
    preds = {(int(r["complex_id"]), r["band"]): r for r in read_csv(RULES / "exit_price_2026.csv")}
    rel = {(int(r["complex_id"]), r["band"]): r for r in read_csv(RULES / "relative_followers.csv")}
    opt = {int(r["complex_id"]): r for r in read_csv(RULES / "option_stage_registry.csv")}
    data, meta = {}, {}
    for c in CASHES:
        rows = [r for r in read_csv(R / f"tw_all_{c}eok.csv") if r.get("tw_rank")]
        if not rows:
            continue
        stp = R / f"tw_stability_all_{c}eok.json"
        st = {}
        if stp.exists():
            for s in json.loads(stp.read_text(encoding="utf-8"))["rows"]:
                st[s["name"] + s["band"]] = s
        out = []
        for r in rows:
            cid = int(r["complex_id"]); band = r["band"]
            p = preds.get((cid, band), {}); rl = rel.get((cid, band), {}); o = opt.get(cid, {}); s = st.get(r["name"] + band, {})
            price = fnum(r["price"]); tw = fnum(r["expected_tw"]); fl = fnum(r["wealth_floor"])
            out.append({
                "id": cid, "n": r["name"], "b": band, "lawd": r["lawd_cd"], "reg": region.get(r["lawd_cd"], r["lawd_cd"]),
                "emd": rl.get("emd", ""), "hh": hh.get(cid), "yr": yr.get(cid),
                "p": round(price / 1e8, 2), "sc": round(fnum(r["self_capital"], 0) / 1e8, 2),
                "bx": round(fnum(r["exit_base"], 0) / price, 3) if price else None,
                "tw": round(tw / 1e8, 2) if tw is not None else None, "fl": round(fl / 1e8, 2) if fl is not None else None,
                "rk": int(r["tw_rank"]), "pred": not r["exit_model"].startswith("NONE"),
                "rel": rl.get("label", ""), "cons": rl.get("consensus", ""),
                "tier": fnum(p.get("tier")), "relm": fnum(p.get("pred_log5y")),
                "ost": int(o["option_stage"]) if o.get("option_stage") not in (None, "") else None,
                "op": o.get("project_probability") if o.get("project_probability") not in (None, "N/A", "") else None,
                "mr": s.get("mean_rank"), "sv": s.get("top10_survival"), "p90": s.get("p90_rank"),
            })
        data[c] = out
        meta[c] = {"n": len(out), "pos": sum(1 for x in out if (x["tw"] or 0) > 0)}
    scen = next(iter(preds.values()), {}).get("market_scenario_note", "")
    probe = None
    p3 = R / "tw_all_3eok.json"
    if p3.exists():
        probe = json.loads(p3.read_text(encoding="utf-8")).get("probe")
    payload = {"data": data, "meta": meta, "scenario": scen, "asof": "2026-09-05", "probe": probe}
    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    page = r"""<title>아파트 엔진</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=Noto+Serif+KR:wght@700&display=swap">
<style>
:root{--bg:#f6f4ee;--paper:#fff;--ink:#22271f;--muted:#66705f;--line:#e0dccf;--accent:#1f6f50;--soft:#e6f1ea;--warn:#9a5b10;--warn-soft:#f8ecd8;--bad:#a13a2f;--good:#1f6f50;--chip:#eef0ea;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#151a16;--paper:#1d231e;--ink:#e8eae3;--muted:#a7ae9f;--line:#333b34;--accent:#7cc7a2;--soft:#213a2d;--warn:#e0a85a;--warn-soft:#3a2d18;--bad:#e08a80;--good:#7cc7a2;--chip:#242b25;}}
:root[data-theme="dark"]{--bg:#151a16;--paper:#1d231e;--ink:#e8eae3;--muted:#a7ae9f;--line:#333b34;--accent:#7cc7a2;--soft:#213a2d;--warn:#e0a85a;--warn-soft:#3a2d18;--bad:#e08a80;--good:#7cc7a2;--chip:#242b25;}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:"Noto Sans KR",system-ui,sans-serif;font-size:15px;line-height:1.5}
.wrap{max-width:720px;margin:0 auto;padding:14px 12px 80px}
h1{font-family:"Noto Serif KR",serif;font-size:1.3rem;margin:0}
.sub{color:var(--muted);font-size:.82rem;margin:2px 0 10px}
.panel{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:10px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.seg{display:flex;gap:4px;flex-wrap:wrap}
.seg button{border:1px solid var(--line);background:var(--chip);color:var(--ink);border-radius:999px;padding:6px 12px;font-size:.9rem;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
select,input[type=text]{border:1px solid var(--line);background:var(--paper);color:var(--ink);border-radius:8px;padding:7px 9px;font-size:.9rem}
input[type=text]{flex:1;min-width:120px}
label.chk{font-size:.86rem;display:flex;gap:4px;align-items:center}
.kpi{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}
.kpi div{background:var(--soft);border-radius:8px;padding:8px 10px}
.kpi b{display:block;font-size:.72rem;color:var(--muted);font-weight:500}
.kpi span{font-size:1.05rem;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:.85rem;font-variant-numeric:tabular-nums}
th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:var(--soft);font-weight:500;position:sticky;top:0}
tr.r{cursor:pointer} tr.r:hover{background:var(--soft)}
.tbl{overflow-x:auto;border-radius:10px}
.good{color:var(--good);font-weight:700}.bad{color:var(--bad);font-weight:700}
.tag{display:inline-block;font-size:.72rem;padding:1px 6px;border-radius:6px;background:var(--chip);color:var(--muted);margin-left:4px}
.warn{background:var(--warn-soft);border-left:4px solid var(--warn);padding:8px 10px;border-radius:8px;font-size:.86rem;margin-bottom:10px}
.detail{position:fixed;left:0;right:0;bottom:0;max-height:70vh;overflow:auto;background:var(--paper);border-top:2px solid var(--accent);padding:14px 16px 24px;box-shadow:0 -6px 24px rgba(0,0,0,.15);z-index:9}
.detail h3{margin:0 0 6px;font-size:1.05rem}
.detail .kv{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:.88rem}
.detail .kv div{background:var(--soft);border-radius:8px;padding:6px 8px}
.detail .kv b{display:block;font-size:.72rem;color:var(--muted);font-weight:500}
.x{float:right;border:0;background:var(--chip);color:var(--ink);border-radius:8px;padding:4px 10px;cursor:pointer}
.muted{color:var(--muted);font-size:.82rem}
.more{width:100%;border:1px dashed var(--line);background:var(--paper);color:var(--accent);padding:8px;border-radius:8px;cursor:pointer;margin-top:6px}
details summary{cursor:pointer;color:var(--accent);font-weight:500}
</style>
<div class="wrap">
<h1>아파트 엔진 <span class="tag">E 이론 · 1,000세대 이상 · 5년 뒤 순자산</span></h1>
<div class="sub" id="sub"></div>

<div class="panel">
  <div class="row"><b style="font-size:.9rem">투자금</b><div class="seg" id="cash"></div></div>
  <div class="row"><b style="font-size:.9rem">지역</b><select id="sido"><option value="">전체</option><option value="11">서울</option><option value="41">경기</option><option value="28">인천</option></select>
    <select id="gu"><option value="">시군구 전체</option></select>
    <b style="font-size:.9rem">면적</b><div class="seg" id="band"></div></div>
  <div class="row"><input type="text" id="q" placeholder="단지명 검색">
    <label class="chk"><input type="checkbox" id="pos"> 순이익 양수만</label>
    <label class="chk"><input type="checkbox" id="predonly" checked> 예측 있는 것만</label>
    <label class="chk"><input type="checkbox" id="dedupe"> 동네별 1개</label></div>
  <div class="row"><b style="font-size:.9rem">정렬</b><select id="sort"><option value="tw">기대 순이익 큰 순</option><option value="fl">최악 시나리오 좋은 순</option><option value="sv">순위 안정성(TOP10 생존율) 순</option><option value="bx">5년 뒤 배율 큰 순</option><option value="p">가격 낮은 순</option></select></div>
</div>

<div class="kpi"><div><b>매수 가능 후보</b><span id="k1">—</span></div><div><b>5년 순이익 양수</b><span id="k2">—</span></div><div><b>표시 중</b><span id="k3">—</span></div></div>
<div class="warn" id="conc" hidden></div>
<div class="panel tbl"><table><thead><tr><th>#</th><th>단지</th><th>지역</th><th>면적</th><th>가격</th><th>실투자</th><th>5년 Base</th><th>기대 순이익</th><th>최악</th><th>생존</th></tr></thead><tbody id="tb"></tbody></table>
<button class="more" id="more" hidden>더 보기</button></div>

<div class="panel"><details><summary>숫자 읽는 법</summary>
<ul class="muted">
<li><b>기대 순이익(TW)</b>: 5년 뒤 예측 매도가에서 취득세·복비·이자·보유세·양도세를 다 뺀 순이익. 표준 조건(비거주·임대, 금리 4%, 공시가 = 매매가×0.65)이라 실제 조건과 다릅니다.</li>
<li><b>최악</b>: 시장이 지금과 비슷했던 과거 중 가장 나빴던 경로에서의 순이익.</li>
<li><b>5년 Base</b>: 5년 뒤 예측 매도가 ÷ 현재가. 시장 수준은 예측이 아니라 "지금과 전세가율·금리가 비슷했던 과거"의 가정입니다.</li>
<li><b>생존</b>: 시장·예측·금리·확률을 300번 흔들어도 TOP10에 남는 비율. 낮으면 한두 가정에 기대는 순위.</li>
<li><b>동네별 1개</b>: 같은 법정동에서 1개만 남깁니다. 한 동네에 순위가 몰리면 그 동네 가정 하나가 틀릴 때 같이 틀립니다.</li>
<li>이 목록은 연구 후보이며 매수 목록이 아닙니다. 실제 매물가·전세·현장 확인 전입니다.</li>
</ul></details></div>
<div class="panel" id="probe"></div>
</div>
<div class="detail" id="detail" hidden></div>
<script>
const D = __DATA__;
let cash = D.data["3"] ? "3" : Object.keys(D.data)[0], band = "", shown = 60;
const $ = id => document.getElementById(id);
const won = v => v == null ? "—" : (v >= 0 ? v.toFixed(2) : v.toFixed(2)) + "억";
const cls = v => v == null ? "" : (v > 0 ? "good" : "bad");
function segs(id, opts, cur, onpick){ const el = $(id); el.innerHTML = ""; opts.forEach(([v, lab]) => { const b = document.createElement("button"); b.textContent = lab; if (v === cur) b.className = "on"; b.onclick = () => onpick(v); el.appendChild(b); }); }
function rows(){ let r = D.data[cash] || []; const sido = $("sido").value, gu = $("gu").value, q = $("q").value.trim();
  if (sido) r = r.filter(x => x.lawd.startsWith(sido)); if (gu) r = r.filter(x => x.lawd === gu); if (band) r = r.filter(x => x.b === band);
  if (q) r = r.filter(x => x.n.includes(q) || x.reg.includes(q) || (x.emd||"").includes(q));
  if ($("pos").checked) r = r.filter(x => (x.tw||0) > 0); if ($("predonly").checked) r = r.filter(x => x.pred);
  const s = $("sort").value; const key = {tw: x => -(x.tw ?? -1e9), fl: x => -(x.fl ?? -1e9), sv: x => -(x.sv ?? -1), bx: x => -(x.bx ?? 0), p: x => x.p}[s]; r = [...r].sort((a,b) => key(a) - key(b));
  if ($("dedupe").checked){ const seen = new Set(); r = r.filter(x => { const k = x.lawd + "|" + x.emd; if (seen.has(k)) return false; seen.add(k); return true; }); }
  return r; }
function render(){ const all = D.data[cash] || []; const r = rows(); const m = D.meta[cash] || {};
  $("k1").textContent = m.n ?? all.length; $("k2").textContent = m.pos ?? all.filter(x => (x.tw||0) > 0).length; $("k3").textContent = r.length;
  const top = r.slice(0, 10); const byEmd = {}; top.forEach(x => { const k = x.reg + " " + (x.emd||""); byEmd[k] = (byEmd[k]||0) + 1; });
  const hot = Object.entries(byEmd).filter(([k, n]) => n >= 4); $("conc").hidden = hot.length === 0 || $("dedupe").checked;
  if (hot.length) $("conc").textContent = "상위 10개 중 " + hot.map(([k,n]) => `${k} ${n}개`).join(", ") + " — 한 동네에 몰린 순위입니다. '동네별 1개'를 켜서 분산해 보세요.";
  const tb = $("tb"); tb.innerHTML = "";
  r.slice(0, shown).forEach((x, i) => { const tr = document.createElement("tr"); tr.className = "r";
    tr.innerHTML = `<td>${i+1}</td><td>${x.n}${x.pred ? "" : '<span class="tag">예측없음</span>'}</td><td>${x.reg}${x.emd ? " " + x.emd : ""}</td><td>${x.b}</td><td>${x.p.toFixed(2)}억</td><td>${x.sc.toFixed(2)}억</td><td>${x.bx ? "×" + x.bx.toFixed(2) : "—"}</td><td class="${cls(x.tw)}">${won(x.tw)}</td><td class="${cls(x.fl)}">${won(x.fl)}</td><td>${x.sv != null ? Math.round(x.sv*100) + "%" : "—"}</td>`;
    tr.onclick = () => detail(x); tb.appendChild(tr); });
  $("more").hidden = r.length <= shown; }
function detail(x){ const d = $("detail"); d.hidden = false;
  d.innerHTML = `<button class="x" onclick="document.getElementById('detail').hidden=true">닫기</button><h3>${x.n} ${x.b}㎡ <span class="tag">${x.reg} ${x.emd||""}</span></h3>
  <div class="kv">
  <div><b>현재 대표가 / 실투자금(${cash}억 프로필)</b>${x.p.toFixed(2)}억 / ${x.sc.toFixed(2)}억</div>
  <div><b>세대수 · 준공</b>${x.hh ?? "—"}세대 · ${x.yr ?? "—"}</div>
  <div><b>5년 뒤 Base 배율</b>${x.bx ? "×" + x.bx.toFixed(2) : "—"} ${x.relm != null ? `(시장 대비 ${(x.relm*100).toFixed(1)}%p)` : ""}</div>
  <div><b>급지</b>${x.tier != null ? x.tier + "급" : "—"}</div>
  <div><b>기대 순이익 / 최악</b><span class="${cls(x.tw)}">${won(x.tw)}</span> / <span class="${cls(x.fl)}">${won(x.fl)}</span></div>
  <div><b>순위 안정성</b>평균 ${x.mr ?? "—"}위 · TOP10 생존 ${x.sv != null ? Math.round(x.sv*100)+"%" : "—"} · 불리할 때 ${x.p90 ?? "—"}위</div>
  <div><b>대장 대비 상대가격</b>${x.rel || "—"} ${x.cons ? "(합의 " + x.cons + ")" : ""}</div>
  <div><b>정비사업</b>${x.ost != null ? "Stage " + x.ost + (x.op ? " · 다음 단계 5년 내 " + Math.round(parseFloat(x.op)*100) + "%" : "") : "등록 없음"}</div>
  </div><p class="muted">예측 모델 ${x.pred ? "있음(E)" : "없음 → 무성장 계산"}. 연구 후보이며 매수 판단은 실제 매물·전세·현장 확인 후.</p>`;
  d.scrollIntoView({behavior: "smooth", block: "end"}); }
function fillGu(){ const sido = $("sido").value; const all = D.data[cash] || []; const gus = {}; all.forEach(x => { if (!sido || x.lawd.startsWith(sido)) gus[x.lawd] = x.reg; });
  const sel = $("gu"); const cur = sel.value; sel.innerHTML = '<option value="">시군구 전체</option>'; Object.entries(gus).sort((a,b) => a[1].localeCompare(b[1])).forEach(([k,v]) => { const o = document.createElement("option"); o.value = k; o.textContent = v; sel.appendChild(o); }); if (gus[cur]) sel.value = cur; }
segs("cash", Object.keys(D.data).map(c => [c, c + "억"]), cash, v => { cash = v; segs("cash", Object.keys(D.data).map(c => [c, c + "억"]), cash, arguments.callee); fillGu(); shown = 60; render(); });
segs("band", [["", "전체"], ["59", "59"], ["74", "74"], ["84", "84"]], band, v => { band = v; segs("band", [["", "전체"], ["59", "59"], ["74", "74"], ["84", "84"]], band, arguments.callee); shown = 60; render(); });
["sido","gu","q","pos","predonly","dedupe","sort"].forEach(id => $(id).addEventListener(id === "q" ? "input" : "change", () => { if (id === "sido") fillGu(); shown = 60; render(); }));
$("more").onclick = () => { shown += 60; render(); };
$("sub").textContent = `${D.asof} · 시장 가정: ${D.scenario || "—"} · 비거주·금리 4% 표준 프로필`;
if (D.probe){ const p = D.probe; $("probe").innerHTML = `<b>회귀 예시 · 부평 동아1단지 74㎡ 4.6억(3억 프로필)</b><div class="muted">5년 뒤 Bear/Base/Bull ${(p.exit_bear/1e8).toFixed(2)} / ${(p.exit_base/1e8).toFixed(2)} / ${(p.exit_bull/1e8).toFixed(2)}억 · 기대 순이익 <span class="${cls(p.expected_tw)}">${won(p.expected_tw/1e8)}</span> · 최악 <span class="${cls(p.wealth_floor)}">${won(p.wealth_floor/1e8)}</span></div>`; }
fillGu(); render();
</script>
"""
    (R / "apt_app.html").write_text(page.replace("__DATA__", js), encoding="utf-8")
    print(R / "apt_app.html", f"{(R / 'apt_app.html').stat().st_size/1024:.0f} KB", {c: meta[c] for c in meta})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
