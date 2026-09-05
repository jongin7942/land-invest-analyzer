"""종인님용 '오늘의 보고' 한 장 — 휴대폰(카톡 링크)에서 위에서 아래로 읽히게.

원칙(answer-format-action-first 메모리): ① 결론 세 줄 ② 종인님이 하실 일 ③ 근거는 접어서. 숫자는 표로, 문장은 짧게.
입력: reports/exit_price_backtest.json, reports/hierarchy_2026.json, reports/tw_combined_2026-09-04.json,
      reports/relative_gap_report.json, rules/exit_price_2026.csv
출력: reports/daily_report.html
"""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "reports"
OUT = R / "daily_report.html"


def load(name, default=None):
    p = R / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def eok(v):
    return "—" if v is None else f"{v/1e8:+.2f}억" if v < 0 else f"{v/1e8:.2f}억"


def pct(v, digits=1):
    return "—" if v is None else f"{v*100:+.{digits}f}%"


def rate(v):
    return "—" if v is None else f"{v*100:.0f}%"


def esc(s):
    return html.escape(str(s))


def main() -> int:
    bt = load("exit_price_backtest_relative.json") or load("exit_price_backtest.json", {})
    hier = load("hierarchy_2026.json", {})
    tw = load("tw_combined_2026-09-04.json", {})
    rel = load("relative_gap_report.json", {})
    mt = load("market_timing.json", {})
    tw1 = load("tw_combined_1eok.json", {})
    st = load("tw_stability_3eok.json", {})
    sc = load("stage_conversion.json", {})
    preds = {}
    p = ROOT / "rules" / "exit_price_2026.csv"
    if p.exists():
        with p.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                preds[(int(r["complex_id"]), r["band"])] = r

    # ── 1. 성적표 ──
    sets = {}
    for key, v in (bt.get("backtest") or {}).items():
        name, lam = key.split("|lam=")
        cur = sets.get(name)
        if cur is None or (v.get("ic_mean") or -9) > (cur[1].get("ic_mean") or -9):
            sets[name] = (lam, v)
    label = {"A_market": "A. 시장만(시군구·수도권 흐름)", "B_+own": "B. + 단지 자기 상태", "C_+theory": "C. + 가격 이론(급지·중심거리·학원·역·공급)", "D_+jobs": "D. + 직장(국민연금 일자리)", "E_+theory2": "E. + 비선형(연식 구간·급지×국면·덜 오른 곳·공급)", "E2_+relmom": "E2. + 상대 위치 변화 속도·확산", "F_+cycle": "F. + 사이클(고점대비·전세가율·금리)", "G_+diffusion": "G. + 확산 전체"}
    rows_bt = ""
    for name in ("A_market", "B_+own", "C_+theory", "D_+jobs", "E_+theory2", "E2_+relmom", "F_+cycle", "G_+diffusion"):
        if name in sets:
            lam, v = sets[name]
            rows_bt += f"<tr><td>{esc(label.get(name, name))}</td><td>{v.get('ic_mean')}</td><td>{rate(v.get('recall_mean'))}</td><td>{v.get('mae_mean')}</td><td>{v.get('mae_market_only_mean')}</td></tr>"
    sel = bt.get("selected") or {}
    coef = bt.get("final_coef") or {}
    top_coef = sorted(coef.items(), key=lambda kv: -abs(kv[1]))[:8]
    coef_rows = "".join(f"<tr><td>{esc(k)}</td><td>{v:+.3f}</td></tr>" for k, v in top_coef)
    dist = bt.get("now_pred_dist") or {}

    # ── 2. 계급도 ──
    gaps = hier.get("tier_gap_pct_now") or {}
    levels = hier.get("tier_level_m2_won") or {}
    counts = hier.get("tier_counts_emd") or {}
    tier_rows = ""
    for tr in sorted(levels, key=lambda x: int(x)):
        gap = gaps.get(f"{int(tr)+1}→{tr}")
        tier_rows += f"<tr><td>{tr}급</td><td>{int(levels[tr])/1e4:,.0f}만원/㎡</td><td>{counts.get(tr, counts.get(str(tr), '—'))}</td><td>{('+%.1f%%' % gap) if gap is not None else '—'}</td></tr>"
    conds = hier.get("conditions") or {}
    cond_rows = "".join(
        f"<tr><td>{esc(c)}</td><td>{v['n']}</td><td>{rate(v['up_rate'])}</td><td>{('%.2f배' % v['lift']) if v.get('lift') else '—'}</td></tr>"
        for c, v in sorted(conds.items(), key=lambda kv: -(kv[1].get("lift") or 0)))
    base_rate = hier.get("promotion_base_rate")

    # ── 3. 동아 ──
    d74 = preds.get((482, "74")) or next((x for x in (bt.get("donga_482") or []) if x.get("band") == "74"), {})
    probe = tw.get("probe") or {}

    # ── 3b. 투자 시점 ──
    mt_rows = ""
    for r in (mt.get("rows") or []):
        if r["year"] < 2011:
            continue
        f = r.get("fwd5_log")
        vol = r.get('metro_vol_ratio'); vol_s = "—" if vol is None else f"{vol:.2f}"
        jr_s = rate(r.get('metro_jeonse_ratio')) if r.get('metro_jeonse_ratio') is not None else "—"
        fwd_s = ('<b>' + pct(f, 0) + '</b>') if f is not None else ('예측 대상' if r['year'] == 2026 else '미확정')
        mt_rows += f"<tr><td>{r['year']}</td><td>{jr_s}</td><td>{vol_s}</td><td>{r.get('bok_rate')}</td><td>{pct(r.get('metro_dd_peak'),0)}</td><td>{pct(r.get('metro_vs_ma5'),0)}</td><td>{fwd_s}</td></tr>"
    mt_rules = mt.get("rules") or {}
    jr = mt_rules.get("metro_jeonse_ratio") or {}
    now = next((r for r in (mt.get("rows") or []) if r["year"] == 2026), {})

    # ── 3c. 1억 · 안정성 ──
    tw1_rows = "".join(
        f"<tr><td>{r['tw_rank']}</td><td>{esc(r['name'])} {r['band']}</td><td>{r['price']/1e8:.2f}억</td><td>{(r.get('self_capital') or 0)/1e8:.2f}억</td><td>{eok(r['expected_tw'])}</td><td>{eok(r['wealth_floor'])}</td></tr>"
        for r in (tw1.get("top20_by_tw") or [])[:8])
    st_rows = "".join(
        f"<tr><td>{r['mean_rank']}</td><td>{r['tw_rank']}</td><td>{esc(r['name'])} {r['band']}</td><td>{r['top10_survival']*100:.0f}%</td><td>{r['p90_rank']}</td></tr>"
        for r in (st.get("rows") or [])[:10])

    # ── 3d. 정비사업 단계 전환율 ──
    sc_rows = ""
    for stage in ("구역지정/추진위(3)", "조합설립(4)", "사업시행인가(5)", "관리처분(6)", "착공(7)"):
        cells = []
        for reg in ("서울", "경기", "인천"):
            v = ((sc.get(reg) or {}).get("by_stage") or {}).get(stage)
            cells.append(f"{v['p_next_within_5y']*100:.0f}% · {v['dwell_median_m']}개월 (n={v['n']})" if v and v.get("p_next_within_5y") is not None else "—")
        sc_rows += f"<tr><td>{esc(stage)}</td><td>{cells[0]}</td><td>{cells[1]}</td><td>{cells[2]}</td></tr>"

    # ── 4. TW 상위 ──
    tw_rows = "".join(
        f"<tr><td>{r['tw_rank']}</td><td>{esc(r['name'])} {r['band']}</td><td>{r['price']/1e8:.2f}억</td><td>{(r.get('exit_base') or 0)/max(r['price'],1):.2f}배</td><td>{eok(r['expected_tw'])}</td><td>{eok(r['wealth_floor'])}</td><td>{r.get('score_rank') or '—'}</td></tr>"
        for r in (tw.get("top20_by_tw") or [])[:10])
    pos = tw.get("positive_tw", sum(1 for r in (tw.get("top20_by_tw") or []) if (r.get("expected_tw") or 0) > 0))
    model_priced = tw.get("model_priced")

    page = f"""<title>아파트 엔진 오늘의 보고</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=Noto+Serif+KR:wght@700&display=swap">
<style>
:root{{--bg:#f6f4ee;--paper:#ffffff;--ink:#22271f;--muted:#66705f;--line:#e0dccf;--accent:#1f6f50;--soft:#e6f1ea;--warn:#9a5b10;--warn-soft:#f8ecd8;--bad:#a13a2f;--good:#1f6f50;}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#151a16;--paper:#1d231e;--ink:#e8eae3;--muted:#a7ae9f;--line:#333b34;--accent:#7cc7a2;--soft:#213a2d;--warn:#e0a85a;--warn-soft:#3a2d18;--bad:#e08a80;--good:#7cc7a2;}}}}
:root[data-theme="dark"]{{--bg:#151a16;--paper:#1d231e;--ink:#e8eae3;--muted:#a7ae9f;--line:#333b34;--accent:#7cc7a2;--soft:#213a2d;--warn:#e0a85a;--warn-soft:#3a2d18;--bad:#e08a80;--good:#7cc7a2;}}
body{{background:var(--bg);color:var(--ink);font-family:"Noto Sans KR",system-ui,sans-serif;line-height:1.55;font-size:15px;}}
.wrap{{max-width:680px;margin:0 auto;padding:18px 14px 60px;}}
h1{{font-family:"Noto Serif KR",serif;font-size:1.35rem;margin:0 0 4px;}}
.date{{color:var(--muted);font-size:.85rem;margin-bottom:14px;}}
.card{{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px;}}
.card h2{{font-size:1.02rem;margin:0 0 8px;color:var(--accent);}}
.big{{font-size:1.05rem;font-weight:500;line-height:1.6;}}
.todo{{background:var(--warn-soft);border-left:4px solid var(--warn);padding:10px 12px;border-radius:8px;}}
table{{border-collapse:collapse;width:100%;font-size:.86rem;font-variant-numeric:tabular-nums;}}
th,td{{border-bottom:1px solid var(--line);padding:6px 6px;text-align:left;vertical-align:top;}}
th{{background:var(--soft);font-weight:500;}}
.tbl{{overflow-x:auto;}}
.kv{{display:grid;grid-template-columns:1fr 1fr;gap:8px;}}
.kv div{{background:var(--soft);border-radius:8px;padding:8px 10px;}}
.kv b{{display:block;font-size:.78rem;color:var(--muted);font-weight:500;}}
.kv span{{font-size:1.05rem;font-weight:700;}}
details summary{{cursor:pointer;color:var(--accent);font-weight:500;margin:6px 0;}}
.muted{{color:var(--muted);font-size:.85rem;}}
.bad{{color:var(--bad);}} .good{{color:var(--good);}}
ul{{padding-left:18px;margin:6px 0;}} li{{margin:3px 0;}}
</style>
<div class="wrap">
<h1>아파트 엔진 오늘의 보고</h1>
<div class="date">2026-09-05 · 1,000세대 이상 단지만 집계·추천 · 5년 뒤 가격 엔진 v0.6(하락기 포함·전세가율 조건부 시장·전세 룩백) · 투자 시점 · 확산 계급 · Terminal Wealth</div>

<div class="card"><h2>한 줄 결론</h2><div class="big">
① 5년 뒤 가격을 맞히는 힘은 <b>{esc(label.get(sel.get('set',''), sel.get('set','')))}</b>이 가장 컸습니다(순위 정확도 IC {sel.get('ic')}). 시장 흐름만 보는 것보다 이론 변수를 넣었을 때 더 잘 맞는지는 아래 표에서 바로 비교됩니다.<br>
② 급지 사이 가격 차이는 데이터가 정했고, 5년 안에 급지가 한 단계 오르는 확률은 {rate(base_rate)}입니다. 그 확률을 실제로 올리는 조건만 '호재'로 인정합니다.<br>
③ 부평 동아1단지 74㎡: 같은 시기 수도권 평균보다 <b>{pct(float(d74.get('pred_log5y')) if d74.get('pred_log5y') is not None else None)}</b> 더 오를 것으로 예측(시장 평균과 거의 같음). 지금과 전세가율이 비슷했던 해(2011·12·21)처럼 시장이 움직이면 5년 뒤 Bear {float(d74.get('bear_factor')):.2f}배 · Base {float(d74.get('base_factor')):.2f}배 · Bull {float(d74.get('bull_factor')):.2f}배 — 시장 수준은 예측이 아니라 '비슷한 국면의 과거' 가정입니다.
</div></div>

<div class="card"><h2>종인님이 하실 일</h2><div class="todo"><b>지금은 없습니다.</b> 보유 vs 갈아타기의 최종 답은 여전히 계약일·대출·전세·거주형태·처분시점·중개사 과세유형·공시가격이 오면 냅니다.</div></div>

<div class="card"><h2>5년 뒤 가격 엔진 성적표 (2013~2021 테스트 · 1,000세대 이상 · 미래 정보 없이)</h2>
<div class="tbl"><table><tr><th>변수군</th><th>순위 정확도 IC</th><th>승자 포착률</th><th>오차 MAE</th><th>시장중앙값만</th></tr>{rows_bt}</table></div>
<p class="muted">IC = 예측 순위와 실제 순위의 상관(0이면 무작위). 승자 포착률 = 실제 상위 10%를 예측 상위 20%가 잡은 비율. MAE = 5년 log 수익률 오차(0.10 ≈ 10%p).</p>
<details><summary>어떤 변수가 가장 크게 작용했나</summary><div class="tbl"><table><tr><th>변수</th><th>표준화 계수</th></tr>{coef_rows}</table></div>
<p class="muted">양수 = 그 변수가 클수록 5년 뒤 더 오름. 학습 표본 {bt.get('final_n')}건.</p></details>
<p class="muted">현재(2026-06 진입) 전 단지의 "시장 대비" 예측 분포: P10 {pct(dist.get('0.1'))} · 중앙 {pct(dist.get('0.5'))} · P90 {pct(dist.get('0.9'))}. 시장 전체 수준(5년)은 <b>지금 전세가율(0.56)과 비슷했던 과거 진입연도(2011·2012·2021)</b>의 최저 +3.7% · 중앙 +10.6% · 최고 +19.9%(log)를 Bear/Base/Bull 로 씁니다 — 예측이 아니라 "비슷한 국면의 과거" 가정입니다(v0.4, 이전 라운드의 전체 분포 +3/+30/+39% 보다 보수적).</p>
</div>

<div class="card"><h2>아파트 계급도 (법정동 급지 8단계)</h2>
<div class="tbl"><table><tr><th>급지</th><th>㎡단가 중앙값</th><th>법정동 수</th><th>한 급지 위와의 차이</th></tr>{tier_rows}</table></div>
<p class="muted">급지 = 최근 24개월 ㎡단가로 데이터가 나눈 8단계(1급이 최고). '차이'는 바로 위 급지 중앙값이 얼마나 더 비싼가.</p>
<h2 style="margin-top:12px">급지가 올라간 조건 = 호재의 정의</h2>
<div class="tbl"><table><tr><th>조건(진입 시점)</th><th>표본</th><th>5년 내 승급률</th><th>기본 대비</th></tr>{cond_rows}</table></div>
<p class="muted">기본 승급률 {rate(base_rate)}. '기본 대비'가 1배를 뚜렷이 넘는 조건만 호재로 인정하고, 그 확률·폭으로만 가격에 넣습니다.</p>
</div>

<div class="card"><h2>투자 시점 — 수도권 전체 사이클 (매년 6월 진입 → 5년 뒤)</h2>
<div class="tbl"><table><tr><th>진입</th><th>전세가율</th><th>거래량비</th><th>기준금리</th><th>고점대비</th><th>5년MA대비</th><th>→ 5년 수익</th></tr>{mt_rows}</table></div>
<p class="muted">5년 뒤 수익과 가장 잘 맞은 시점 변수는 <b>전세가율</b>(순위상관 {jr.get('spearman')}, 0.70 이상이면 중앙 {pct(jr.get('fwd_when_high'),0)}, 미만이면 {pct(jr.get('fwd_when_low'),0)})이고, 그다음이 낮은 기준금리였습니다. 가격이 이미 많이 오른 뒤(2021, 5년MA 대비 +41%)의 진입은 +3%에 그쳤습니다. <b>지금(2026-06)</b>: 전세가율 {rate(now.get('metro_jeonse_ratio'))}로 시계열 최저, 거래량은 회복기 수준({now.get('metro_vol_ratio')}), 고점 대비 {pct(now.get('metro_dd_peak'),0)}. "거래는 살아났지만 전세가 받치지 않는" 국면이라 시장 수준 Base 가정(+35%)보다 신중하게 봐야 합니다. 표본 15개(한 사이클)라 확정 규칙은 아닙니다.</p>
</div>

<div class="card"><h2>부평 동아1단지 74㎡ (4.6억 저층)</h2>
<div class="kv">
<div><b>시장 대비 5년 예측</b><span>{pct(float(d74.get('pred_log5y')) if d74.get('pred_log5y') is not None else None)}</span></div>
<div><b>급지 / 상위급지 중심까지</b><span>{int(float(d74.get('tier')))}급 / {d74.get('dist_center_km')}km</span></div>
<div><b>EXPECTED_TW(표준 프로필)</b><span class="{'good' if (probe.get('expected_tw') or 0)>0 else 'bad'}">{eok(probe.get('expected_tw'))}</span></div>
<div><b>Wealth Floor(Bear)</b><span class="bad">{eok(probe.get('wealth_floor'))}</span></div>
</div>
<p class="muted">표준 프로필(3억·비거주·5년·금리 4%·공시가 = 매매가×0.65)은 후보 비교용이지 종인님 실제 조건이 아닙니다. 매도가 Bear/Base/Bull {eok(probe.get('exit_bear'))} / {eok(probe.get('exit_base'))} / {eok(probe.get('exit_bull'))}.</p>
</div>

<div class="card"><h2>5년 뒤 순자산이 가장 큰 후보 10 (3억 프로필 · 1,000세대 이상 · 점수 TOP100 풀)</h2>
<div class="tbl"><table><tr><th>TW순위</th><th>단지</th><th>가격</th><th>5년 뒤 Base</th><th>기대 순이익</th><th>최악(Bear)</th><th>점수순위</th></tr>{tw_rows}</table></div>
<p class="muted">100개 중 가격 엔진 예측이 붙은 후보 {model_priced}개, 기대 순이익 양수 {pos}개. 세금·이자·복비를 다 뺀 5년 뒤 순이익(3억 프로필, 금리 4%)입니다. 예측이 없는 후보는 무성장으로 계산돼 뒤로 밀립니다. 아직 '연구 후보'이며 실제 매물·전세 확인 전입니다.</p>
</div>

<div class="card"><h2>재건축·재개발은 실제로 얼마나 진행되나 (단계별 5년 내 다음 단계 도달률 · 체류기간)</h2>
<div class="tbl"><table><tr><th>지금 단계</th><th>서울</th><th>경기</th><th>인천</th></tr>{sc_rows}</table></div>
<p class="muted">서울 정비사업 733건·경기 349건·인천 87건의 실제 일자로 잰 값. 서울은 조합설립 뒤가 가장 느립니다(5년 안에 사업시행인가 60%, 중앙 33개월). 관리처분 이후는 80~95%로 사실상 되돌아가지 않습니다. 이 확률이 정비사업 옵션의 '사업확률' 자리에 들어갔습니다(완공 확률이 아니라 다음 단계 도달 확률).</p>
</div>

<div class="card"><h2>순위가 얼마나 흔들리나 (3억 · 시장·예측·금리·확률을 300번 흔든 결과)</h2>
<div class="tbl"><table><tr><th>평균순위</th><th>기본 TW순위</th><th>단지</th><th>TOP10 생존율</th><th>P90(불리할 때)</th></tr>{st_rows}</table></div>
<p class="muted">TOP10 생존율이 낮은 후보는 한두 가정에 기대는 순위입니다. 순이익은 세 시나리오 점의 선형 근사라 정확한 재계산은 아닙니다.</p>
</div>

<div class="card"><h2>자기자본 1억 — 후보 {tw1.get('computed', 0)}개 중 상위</h2>
<div class="tbl"><table><tr><th>TW순위</th><th>단지</th><th>가격</th><th>실투자</th><th>기대 순이익</th><th>최악(Bear)</th></tr>{tw1_rows}</table></div>
<p class="muted">1억으로 실투자금이 맞는 1,000세대 이상 단지는 24개뿐(최고가 3.1억). 기대 순이익 양수 {tw1.get('positive_tw', 0)}개.</p>
</div>

<div class="card"><h2>이번에 바뀐 것</h2><ul>
<li>5년 뒤 가격 엔진이 처음 생겼습니다. 이론(직장·끼리끼리·교육환경·차선책·선점)을 변수로 옮겨 2011~2021 진입을 미래 정보 없이 검증했습니다.</li>
<li>직장 변수는 국민연금 가입 사업장(수도권 570만→707만 명, 2016~2026)으로 만들었습니다.</li>
<li>급지 계급도와 '급지 상승 조건'을 데이터로 정했습니다.</li>
<li>Terminal Wealth 가 무성장 가정을 벗어나 예측 가격으로 계산됩니다.</li>
<li><b>1,000세대 미만 단지는 집계·추천에서 제외</b>했습니다(종인님 규칙). 대상 단지 10,123 → 1,478.</li>
<li>확산 계급 검증: 선행지·빠른추종지는 다음 해에도 61~62% 같은 계급(정해져 있음). 후행지는 5년 뒤에도 시장 대비 −7%(상승국면 −14.5%) — '나중에 오른다'가 아니라 '덜 오른다'. 상승국면 승자는 빠른 추종(+3.3%).</li>
</ul>
<p class="muted">상세(스펙 전문·연구로그·체크리스트)는 별도 페이지: <a href="https://claude.ai/code/artifact/1acc5db5-80be-41bc-b95e-d6d81392908d">MASTER SPEC 병합 보고</a></p>
</div>
</div>
"""
    OUT.write_text(page, encoding="utf-8")
    print(OUT, f"{OUT.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
