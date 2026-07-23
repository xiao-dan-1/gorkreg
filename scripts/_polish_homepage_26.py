# -*- coding: utf-8 -*-
"""GrokX homepage 2.6 — bold Vercel/Linear-inspired command deck.

Preserves all element IDs used by main.js. Idempotent on re-run for CSS marker.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def polish_html() -> None:
    html_path = ROOT / "client" / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    # --- full overview section replace (IDs preserved) ---
    start = html.find('<section class="page active" id="page-overview">')
    if start < 0:
        start = html.find('id="page-overview"')
        if start < 0:
            raise SystemExit("page-overview not found")
        start = html.rfind("<section", 0, start)
    end = html.find("<!-- Register:", start)
    if end < 0:
        end = html.find('<section class="page" id="page-register">', start)
    if start < 0 or end < 0:
        raise SystemExit(f"bounds fail start={start} end={end}")

    ov = r'''            <section class="page active" id="page-overview">
        <div class="ov-shell">
          <header class="ov-top">
            <div class="ov-top-left">
              <div class="ov-brand">GrokX</div>
              <div class="ov-brand-sub muted">指挥台 · 一屏决策</div>
            </div>
            <div class="ov-top-right">
              <span class="ov-ver" id="ov-version">v-</span>
              <button type="button" class="btn btn-ghost btn-sm ov-refresh" onclick="refreshOverview()">刷新</button>
            </div>
          </header>

          <!-- Command: single decision -->
          <div class="ov-hero ov-hero-v26" id="ov-hero">
            <div class="ov-hero-body">
              <div class="ov-hero-kicker">
                <span class="ov-dot" aria-hidden="true"></span>
                <span class="ov-hero-badge" id="ov-hero-badge">池状态</span>
              </div>
              <div class="ov-hero-title" id="ov-hero-title">分析库存…</div>
              <div class="ov-hero-desc" id="ov-hero-desc">根据缺口 / needs / 可交付 / Cap 自动建议</div>
              <div class="ov-hero-warn muted" id="ov-hero-warn" style="display:none;"></div>
            </div>
            <div class="ov-hero-actions">
              <button type="button" class="btn btn-primary btn-hero" id="ov-hero-btn" onclick="runOverviewRecommended()">执行推荐</button>
              <button type="button" class="btn btn-quiet" id="ov-hero-secondary" onclick="switchPage('ops')">运维台</button>
            </div>
          </div>

          <!-- KPI strip: number-first, no rainbow icons -->
          <div class="ov-kpi" id="ov-kpi-row">
            <button type="button" class="ov-kpi-cell" onclick="overviewGo('auth')" title="凭证账本 auth.json">
              <span class="ov-kpi-label">账本</span>
              <span class="ov-kpi-value" id="ov-auth">-</span>
              <span class="ov-kpi-sub muted" id="ov-auth-sub">roster · fresh</span>
            </button>
            <button type="button" class="ov-kpi-cell" id="ov-kpi-cpa" onclick="overviewGo('cpa')" title="export 包">
              <span class="ov-kpi-label">可交付</span>
              <span class="ov-kpi-value" id="ov-cpa">-</span>
              <span class="ov-kpi-sub muted">cpa pack</span>
            </button>
            <button type="button" class="ov-kpi-cell" id="ov-kpi-gap" onclick="overviewGo('gap')" title="待 mint">
              <span class="ov-kpi-label">缺口</span>
              <span class="ov-kpi-value" id="ov-gap">-</span>
              <span class="ov-kpi-sub muted" id="ov-gap-sub">mint-missing</span>
            </button>
            <button type="button" class="ov-kpi-cell" id="ov-kpi-cap" onclick="switchPage('captcha')" title="CapSolver 余额">
              <span class="ov-kpi-label">Cap $</span>
              <span class="ov-kpi-value" id="ov-cap-bal">-</span>
              <span class="ov-kpi-sub muted" id="ov-cap-sub">打码钱包</span>
            </button>
          </div>

          <!-- hidden mirrors for scripts -->
          <span id="ov-roster" class="visually-hidden" aria-hidden="true">0</span>
          <span id="ov-needs" class="visually-hidden" aria-hidden="true">0</span>
          <span id="ov-fresh" class="visually-hidden" aria-hidden="true">0</span>
          <!-- keep legacy icon target so JS class toggles don't throw -->
          <span id="ov-gap-icon" class="visually-hidden" aria-hidden="true"></span>

          <!-- Live task stage -->
          <div class="ov-stage" id="ov-current-task-card">
            <div class="ov-stage-head">
              <div class="ov-stage-title-row">
                <span class="ov-stage-title">当前任务</span>
                <span class="badge badge-idle" id="ov-task-badge">空闲</span>
                <span class="ov-stage-kind muted" id="ov-task-kind-label"></span>
              </div>
              <div class="ov-stage-actions">
                <button type="button" class="btn btn-danger btn-sm" id="btn-global-stop" onclick="stopTask()" disabled style="display:none;">停止</button>
                <button type="button" class="btn btn-ghost btn-sm" onclick="switchPage('logs')">日志</button>
              </div>
            </div>

            <div class="ov-stage-progress">
              <div class="ov-stage-progress-meta">
                <span class="ov-stage-progress-label">进度</span>
                <span class="ov-stage-progress-val" id="ov-task-progress">0/0</span>
              </div>
              <div class="progress-wrap ov-live-bar"><div class="progress-bar" id="ov-progress" style="width:0%"></div></div>
            </div>

            <div class="ov-stage-stats">
              <div class="ov-stage-stat">
                <span class="ov-stage-stat-k">成功</span>
                <span class="ov-stage-stat-v is-ok" id="ov-task-ok-pill">0</span>
              </div>
              <div class="ov-stage-stat">
                <span class="ov-stage-stat-k">失败</span>
                <span class="ov-stage-stat-v is-fail" id="ov-task-fail-pill">0</span>
              </div>
              <div class="ov-stage-stat">
                <span class="ov-stage-stat-k">耗时</span>
                <span class="ov-stage-stat-v" id="ov-task-elapsed">0s</span>
              </div>
              <div class="ov-stage-stat">
                <span class="ov-stage-stat-k">吞吐</span>
                <span class="ov-stage-stat-v" id="ov-task-thr">—</span>
              </div>
              <div class="ov-stage-stat is-soft">
                <span class="ov-stage-stat-k">成功率</span>
                <span class="ov-stage-stat-v" id="ov-task-rate">—</span>
              </div>
              <div class="ov-stage-stat is-soft">
                <span class="ov-stage-stat-k">均耗</span>
                <span class="ov-stage-stat-v" id="ov-task-avg">—</span>
              </div>
              <div class="ov-stage-stat is-soft">
                <span class="ov-stage-stat-k">剩余</span>
                <span class="ov-stage-stat-v" id="ov-task-eta">—</span>
              </div>
              <div class="ov-stage-stat is-soft">
                <span class="ov-stage-stat-k">类型</span>
                <span class="ov-stage-stat-v" id="ov-task-kind">—</span>
              </div>
            </div>

            <div class="ov-stage-msg muted" id="ov-task-msg">空闲 · 用上方主按钮开始推荐动作</div>
            <div class="ov-live-last muted" id="ov-task-last" style="display:none;"></div>
            <div class="ov-live-cli muted" id="ov-task-cli-summary" style="display:none;"></div>

            <!-- hidden stubs for JS -->
            <div id="ov-quick-actions" class="visually-hidden" aria-hidden="true">
              <button type="button" id="ov-quick-primary" onclick="runOverviewRecommended()"></button>
              <button type="button" id="ov-quick-secondary"></button>
              <button type="button" id="ov-quick-tertiary"></button>
            </div>
            <div id="ov-task-story" class="visually-hidden" aria-hidden="true"></div>
            <div id="ov-task-sf" class="visually-hidden" aria-hidden="true"></div>
            <div id="ov-task-details" style="display:none;"></div>
            <div class="check-grid" id="ov-env-grid" style="display:none;"></div>
          </div>

          <!-- Secondary folds -->
          <div class="ov-more" id="ov-more">
            <details class="ov-fold" id="ov-fold-eff">
              <summary>
                <span>效率</span>
                <span class="muted ov-fold-summary" id="ov-eff-summary">注册 — · Mint —</span>
              </summary>
              <div class="ov-eff-grid" id="ov-eff-row">
                <div class="ov-eff-cell">
                  <div class="ov-kpi-label">注册</div>
                  <div class="ov-kpi-value ov-eff-num" id="ov-reg-rate">—</div>
                  <div class="ov-kpi-sub muted" id="ov-reg-eff-sub">近批 ok% · thr</div>
                </div>
                <div class="ov-eff-cell">
                  <div class="ov-kpi-label">Mint</div>
                  <div class="ov-kpi-value ov-eff-num" id="ov-mint-rate">—</div>
                  <div class="ov-kpi-sub muted" id="ov-mint-eff-sub">近次汇总</div>
                </div>
              </div>
            </details>

            <details class="ov-fold" id="ov-health-strip">
              <summary>
                <span>就绪</span>
                <span class="muted ov-fold-summary" id="hs-ready-line">检测中…</span>
              </summary>
              <div class="health-strip-actions ov-fold-actions">
                <button class="btn btn-secondary btn-sm" type="button" onclick="loadEnvCheck()">刷新</button>
                <button class="btn btn-secondary btn-sm" type="button" onclick="runCheckChain()">测代理链</button>
              </div>
              <div class="health-strip-grid health-strip-clean" id="ov-health-strip-grid">
                <div class="health-chip"><span class="hc-k">打码</span><span class="hc-v" id="hs-backend">-</span></div>
                <div class="health-chip" title="CapSolver"><span class="hc-k">Cap</span><span class="hc-v" id="hs-cap">-</span></div>
                <div class="health-chip" style="display:none;"><span class="hc-k">Yes</span><span class="hc-v" id="hs-yes">-</span></div>
                <div class="health-chip"><span class="hc-k">代理</span><span class="hc-v" id="hs-proxy">-</span></div>
                <div class="health-chip"><span class="hc-k">注册</span><span class="hc-v" id="hs-reg">-</span></div>
                <div class="health-chip"><span class="hc-k">Mint</span><span class="hc-v" id="hs-mint">-</span></div>
              </div>
            </details>

            <details class="ov-fold" id="ov-batch-debug-card">
              <summary>
                <span>最近注册批</span>
                <span class="muted ov-fold-summary" id="ov-batch-debug-name">—</span>
              </summary>
              <div class="ov-recent-headline" id="ov-batch-headline">
                <div class="muted">近 3 批 · ok% / thr</div>
              </div>
              <div id="ov-batch-debug" style="display:none;"></div>
              <div class="ov-debug-buckets" id="ov-batch-buckets"></div>
              <table class="table table-compact ov-batch-table">
                <thead>
                  <tr>
                    <th>批次</th>
                    <th>成功</th>
                    <th>ok%</th>
                    <th>j</th>
                    <th>墙钟</th>
                    <th>吞吐</th>
                  </tr>
                </thead>
                <tbody id="ov-batches"><tr><td colspan="6" class="muted">加载中…</td></tr></tbody>
              </table>
            </details>
          </div>
        </div>
      </section>

'''
    html = html[:start] + ov + html[end:]
    html_path.write_text(html, encoding="utf-8")
    print("html ok")


CSS_26 = r"""
/* —— Homepage 2.6 command deck (Vercel light + Linear precision) —— */
/* Number-first · monochrome chrome · one accent · no rainbow toys */

#page-overview {
  max-width: 960px;
  padding: 28px 28px 56px;
  /* calmer canvas than global purple gradient */
  background: transparent;
}

#page-overview .ov-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* —— top bar —— */
#page-overview .ov-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 2px;
}
#page-overview .ov-brand {
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.5px;
  color: #0f172a;
  line-height: 1.1;
}
#page-overview .ov-brand-sub {
  margin-top: 4px;
  font-size: 12px;
  font-weight: 450;
  color: #64748b;
}
#page-overview .ov-top-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
#page-overview .ov-ver {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  color: #64748b;
  background: #fff;
  border: 1px solid rgba(15,23,42,0.08);
  border-radius: 999px;
  padding: 4px 10px;
}
#page-overview .ov-refresh {
  color: #64748b;
}

/* —— hero —— */
#page-overview .ov-hero-v26 {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px 20px;
  padding: 20px 22px;
  background: #fff;
  border-radius: 14px;
  /* Vercel shadow-as-border */
  box-shadow:
    0 0 0 1px rgba(15, 23, 42, 0.06),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 12px 28px -16px rgba(15, 23, 42, 0.12);
  position: relative;
  overflow: hidden;
}
#page-overview .ov-hero-v26::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, #5e6ad2, #7170ff);
  border-radius: 14px 0 0 14px;
}
#page-overview .ov-hero-body {
  min-width: 0;
  flex: 1;
  padding-left: 6px;
}
#page-overview .ov-hero-kicker {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
#page-overview .ov-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5e6ad2;
  box-shadow: 0 0 0 3px rgba(94, 106, 210, 0.18);
}
#page-overview .ov-hero-badge {
  font-size: 11px;
  font-weight: 550;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #5e6ad2;
  background: transparent;
  padding: 0;
  margin: 0;
}
#page-overview .ov-hero-title {
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.4px;
  color: #0f172a;
  line-height: 1.25;
  margin-bottom: 6px;
}
#page-overview .ov-hero-desc {
  font-size: 13px;
  line-height: 1.5;
  color: #64748b;
  max-width: 520px;
}
#page-overview .ov-hero-warn {
  margin-top: 10px;
  font-size: 12px;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 8px 10px;
  border-radius: 8px;
  max-width: 520px;
}
#page-overview .ov-hero-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
#page-overview .btn-hero {
  min-height: 40px;
  padding: 0 18px;
  font-size: 13.5px;
  font-weight: 600;
  border-radius: 8px;
  background: #5e6ad2 !important;
  border: none !important;
  color: #fff !important;
  box-shadow: 0 1px 2px rgba(15,23,42,0.08), 0 6px 16px -6px rgba(94,106,210,0.55);
}
#page-overview .btn-hero:hover:not(:disabled) {
  background: #7170ff !important;
  filter: none;
}
#page-overview .btn-quiet {
  min-height: 40px;
  padding: 0 14px;
  font-size: 13px;
  font-weight: 500;
  border-radius: 8px;
  background: #fff;
  color: #334155;
  border: 1px solid rgba(15,23,42,0.1);
  box-shadow: none;
}
#page-overview .btn-quiet:hover {
  background: #f8fafc;
  border-color: rgba(15,23,42,0.16);
}

/* —— KPI: number-first grid —— */
#page-overview .ov-kpi {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 820px) {
  #page-overview .ov-kpi { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
#page-overview .ov-kpi-cell {
  appearance: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: #fff;
  border: none;
  border-radius: 12px;
  box-shadow: 0 0 0 1px rgba(15,23,42,0.06);
  transition: box-shadow .15s, transform .12s;
  color: inherit;
}
#page-overview .ov-kpi-cell:hover {
  transform: translateY(-1px);
  box-shadow:
    0 0 0 1px rgba(94,106,210,0.28),
    0 8px 20px -12px rgba(15,23,42,0.18);
}
#page-overview .ov-kpi-cell:focus-visible {
  outline: 2px solid #5e6ad2;
  outline-offset: 2px;
}
#page-overview .ov-kpi-label {
  font-size: 11px;
  font-weight: 550;
  color: #94a3b8;
  letter-spacing: 0.02em;
}
#page-overview .ov-kpi-value {
  font-size: 26px;
  font-weight: 650;
  letter-spacing: -0.6px;
  font-variant-numeric: tabular-nums;
  color: #0f172a;
  line-height: 1.1;
}
#page-overview .ov-kpi-sub {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
  line-height: 1.3;
}
/* state tones via existing JS classes on cells/ids */
#page-overview .ov-kpi-cell.stat-alert .ov-kpi-value,
#page-overview #ov-kpi-gap.stat-alert .ov-kpi-value {
  color: #dc2626;
}
#page-overview .ov-kpi-cell.stat-ok .ov-kpi-value,
#page-overview #ov-kpi-gap.stat-muted-gap .ov-kpi-value {
  color: #059669;
}
#page-overview .ov-kpi-cell.stat-warn .ov-kpi-value {
  color: #d97706;
}
#page-overview .ov-kpi-cell.stat-priority {
  box-shadow: 0 0 0 1px rgba(220,38,38,0.22);
}
/* JS still toggles .stat-card classes on buttons — bridge */
#page-overview .ov-kpi-cell.stat-card { /* no-op bridge */ }

/* —— stage (task) —— */
#page-overview .ov-stage {
  background: #fff;
  border-radius: 14px;
  padding: 16px 18px 14px;
  box-shadow: 0 0 0 1px rgba(15,23,42,0.06);
}
#page-overview .ov-stage-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}
#page-overview .ov-stage-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
#page-overview .ov-stage-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}
#page-overview .ov-stage-kind {
  font-size: 12px;
  color: #94a3b8;
}
#page-overview .ov-stage-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}
#page-overview .ov-stage-progress-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}
#page-overview .ov-stage-progress-label {
  font-size: 11px;
  font-weight: 550;
  color: #94a3b8;
}
#page-overview .ov-stage-progress-val {
  font-size: 13px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: #0f172a;
  font-family: var(--font-mono);
}
#page-overview .ov-live-bar {
  height: 6px;
  border-radius: 999px;
  background: #f1f5f9;
  border: none;
  overflow: hidden;
  margin-bottom: 14px;
}
#page-overview .ov-live-bar .progress-bar {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #5e6ad2, #818cf8);
  box-shadow: none;
  transition: width 0.25s ease;
}

#page-overview .ov-stage-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px 10px;
}
@media (max-width: 640px) {
  #page-overview .ov-stage-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
#page-overview .ov-stage-stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fafc;
}
#page-overview .ov-stage-stat.is-soft {
  background: transparent;
  padding: 6px 8px;
}
#page-overview .ov-stage-stat-k {
  font-size: 11px;
  font-weight: 500;
  color: #94a3b8;
}
#page-overview .ov-stage-stat-v {
  font-size: 16px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.3px;
  color: #0f172a;
  font-family: var(--font-mono);
}
#page-overview .ov-stage-stat.is-soft .ov-stage-stat-v {
  font-size: 13px;
  font-weight: 550;
  color: #475569;
}
#page-overview .ov-stage-stat-v.is-ok,
#page-overview #ov-task-ok-pill {
  color: #059669 !important;
  background: transparent !important;
  min-width: 0 !important;
  height: auto !important;
  padding: 0 !important;
  border-radius: 0 !important;
  display: inline !important;
  font-size: 16px !important;
  font-weight: 650 !important;
}
#page-overview .ov-stage-stat-v.is-fail,
#page-overview #ov-task-fail-pill {
  color: #dc2626 !important;
  background: transparent !important;
  min-width: 0 !important;
  height: auto !important;
  padding: 0 !important;
  border-radius: 0 !important;
  display: inline !important;
  font-size: 16px !important;
  font-weight: 650 !important;
}
/* neutralize old pill classes if JS re-applies */
#page-overview .ov-live-ok,
#page-overview .ov-live-fail {
  background: transparent !important;
  color: inherit;
}

#page-overview .ov-stage-msg {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(15,23,42,0.06);
  font-size: 12.5px;
  color: #64748b;
  line-height: 1.45;
}
#page-overview .ov-live-last,
#page-overview .ov-live-cli {
  margin-top: 6px;
  font-size: 11px;
  color: #94a3b8;
}

/* badges on overview */
#page-overview .badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
}
#page-overview .badge-running {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
  animation: none;
  box-shadow: none;
}
#page-overview .badge-done {
  background: rgba(94, 106, 210, 0.1);
  color: #5e6ad2;
}
#page-overview .badge-idle {
  background: #f1f5f9;
  color: #94a3b8;
}

/* —— folds —— */
#page-overview .ov-more {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 2px;
}
#page-overview .ov-fold {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 0 0 1px rgba(15,23,42,0.06);
  padding: 0;
  overflow: hidden;
}
#page-overview .ov-fold > summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  font-size: 13px;
  font-weight: 550;
  color: #334155;
  user-select: none;
}
#page-overview .ov-fold > summary::-webkit-details-marker { display: none; }
#page-overview .ov-fold > summary::before {
  content: "";
  width: 5px;
  height: 5px;
  border-right: 1.5px solid #94a3b8;
  border-bottom: 1.5px solid #94a3b8;
  transform: rotate(-45deg);
  transition: transform 0.15s ease;
  flex-shrink: 0;
  margin-top: -1px;
}
#page-overview .ov-fold[open] > summary::before { transform: rotate(45deg); }
#page-overview .ov-fold > summary:hover { background: #fafafa; }
#page-overview .ov-fold-summary {
  margin-left: auto;
  font-weight: 450;
  font-size: 12px;
  color: #94a3b8;
  max-width: 58%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
#page-overview .ov-fold-actions {
  display: flex;
  gap: 8px;
  padding: 0 14px 8px;
}
#page-overview .ov-eff-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  padding: 0 14px 14px;
}
#page-overview .ov-eff-cell {
  padding: 12px 14px;
  border-radius: 10px;
  background: #f8fafc;
}
#page-overview .ov-eff-num {
  font-size: 22px !important;
}
#page-overview .health-strip-grid {
  padding: 0 14px 14px;
  gap: 8px;
}
#page-overview .health-chip {
  background: #f8fafc;
  border: 1px solid rgba(15,23,42,0.05);
  border-radius: 10px;
}
#page-overview .ov-recent-headline {
  padding: 0 14px 8px;
}
#page-overview .ov-debug-buckets {
  padding: 0 14px;
  font-size: 12px;
  color: #64748b;
}
#page-overview .ov-batch-table {
  margin: 0 14px 12px;
  width: calc(100% - 28px);
}
#page-overview .table-compact th {
  font-size: 11px;
  font-weight: 550;
  color: #94a3b8;
  border-bottom: 1px solid rgba(15,23,42,0.06);
  background: transparent;
}
#page-overview .table-compact td {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  border-bottom: 1px solid rgba(15,23,42,0.04);
  color: #334155;
}
#page-overview .table-compact tr:last-child td { border-bottom: none; }

/* calm the page chrome vs global purple wash */
.content:has(#page-overview.active) {
  background:
    radial-gradient(900px 420px at 10% -10%, rgba(94,106,210,0.07), transparent 55%),
    radial-gradient(700px 360px at 90% 0%, rgba(14,165,233,0.05), transparent 50%),
    #f4f5f7;
}

/* dark */
[data-theme="dark"] #page-overview .ov-brand { color: #f1f5f9; }
[data-theme="dark"] #page-overview .ov-hero-v26,
[data-theme="dark"] #page-overview .ov-kpi-cell,
[data-theme="dark"] #page-overview .ov-stage,
[data-theme="dark"] #page-overview .ov-fold {
  background: #14151a;
  box-shadow: 0 0 0 1px rgba(255,255,255,0.07);
}
[data-theme="dark"] #page-overview .ov-hero-title,
[data-theme="dark"] #page-overview .ov-kpi-value,
[data-theme="dark"] #page-overview .ov-stage-title,
[data-theme="dark"] #page-overview .ov-stage-stat-v {
  color: #f1f5f9;
}
[data-theme="dark"] #page-overview .ov-stage-stat { background: rgba(255,255,255,0.04); }
[data-theme="dark"] #page-overview .btn-quiet {
  background: transparent;
  color: #e2e8f0;
  border-color: rgba(255,255,255,0.1);
}
[data-theme="dark"] .content:has(#page-overview.active) {
  background: #0b0c0f;
}

@media (max-width: 720px) {
  #page-overview { padding: 18px 16px 40px; }
  #page-overview .ov-hero-v26 { flex-direction: column; align-items: stretch; }
  #page-overview .ov-hero-actions { width: 100%; }
  #page-overview .ov-hero-actions .btn { flex: 1; justify-content: center; }
}
"""


def polish_css() -> None:
    css_path = ROOT / "client" / "static" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    # strip previous overview polish blocks
    for marker in (
        "/* —— Homepage 2.6 command deck",
        "/* —— Homepage 2.5.2 polish —— */",
    ):
        if marker in css:
            css = css[: css.find(marker)].rstrip() + "\n"
    css_path.write_text(css.rstrip() + "\n" + CSS_26, encoding="utf-8")
    print("css ok", css_path.stat().st_size)


def polish_js_bridge() -> None:
    """JS still toggles .stat-card / .stat-icon classes on KPI — bridge to new cells."""
    main = ROOT / "client" / "static" / "js" / "main.js"
    t = main.read_text(encoding="utf-8")
    # ensure thr fallback when elapsed known (minor UX)
    # no structural JS change required if IDs match
    # Bridge: updateOverviewHero may set className on ov-kpi-gap etc — IDs preserved.
    # Fix thr=— when completed: if thr null and completed+elapsed, compute
    old = """    if ($("ov-task-thr"))
      $("ov-task-thr").textContent = thr != null ? thr + "/s" : "—";"""
    # handle both CRLF and possible formatting
    if "ov-task-thr" in t and "thr != null ? thr + \"/s\"" in t:
        import re
        t2, n = re.subn(
            r'if \(\$\("ov-task-thr"\)\)\s*\$\("ov-task-thr"\)\.textContent = thr != null \? thr \+ "/s" : "—";',
            '''if ($("ov-task-thr")) {
      var thrShow = thr;
      if (thrShow == null && elapsed > 0 && completed > 0) thrShow = Math.round((completed / elapsed) * 100) / 100;
      $("ov-task-thr").textContent = thrShow != null ? thrShow + "/s" : "—";
    }''',
            t,
            count=1,
        )
        if n:
            main.write_text(t2, encoding="utf-8")
            print("js thr fallback ok")
        else:
            print("js thr pattern miss (ok if already)")
    else:
        print("js thr skip")


def polish_tests() -> None:
    p = ROOT / "tests" / "test_client_homepage_24.py"
    t = p.read_text(encoding="utf-8")
    if "2.6 command deck" in t:
        print("test already")
        return
    add = '''


def test_homepage_26_structure():
    """2.6 number-first deck: IDs preserved, no rainbow icons, single hero CTA."""
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    css = Path("client/static/style.css").read_text(encoding="utf-8")
    assert 'id="page-overview"' in html
    assert 'id="ov-hero"' in html
    assert 'id="ov-hero-btn"' in html
    assert 'id="ov-auth"' in html
    assert 'id="ov-cpa"' in html
    assert 'id="ov-gap"' in html
    assert 'id="ov-cap-bal"' in html
    assert 'id="ov-task-ok-pill"' in html
    assert 'id="ov-task-fail-pill"' in html
    assert 'id="ov-task-progress"' in html
    assert 'id="ov-progress"' in html
    assert "ov-kpi-cell" in html
    assert "ov-stage" in html
    assert "icon-green" not in html.split("page-register")[0]  # overview drop rainbow icons
    assert "ov-live-start" not in html
    assert "Homepage 2.6 command deck" in css
'''
    p.write_text(t.rstrip() + add + "\n", encoding="utf-8")
    print("test updated")


if __name__ == "__main__":
    polish_html()
    polish_css()
    polish_js_bridge()
    polish_tests()
    print("done")
