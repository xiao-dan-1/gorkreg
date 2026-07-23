# -*- coding: utf-8 -*-
"""One-shot homepage 2.5.2 polish: HTML structure + CSS, preserve IDs."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def polish_html() -> None:
    html_path = ROOT / "client" / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    old = html

    ov_old = """            <section class=\"page active\" id=\"page-overview\">
        <div class=\"db-header\">
          <div class=\"db-title\">GrokX</div>
          <div style=\"display:flex;gap:8px;align-items:center;\">
            <span class=\"db-version-tag\" id=\"ov-version\">v-</span>
            <button class=\"btn btn-secondary\" onclick=\"refreshOverview()\">刷新</button>
          </div>
        </div>

        <!-- Command deck: single decision surface -->
        <div class=\"ov-hero card\" id=\"ov-hero\">
          <div class=\"ov-hero-body\">
            <div class=\"ov-hero-badge\" id=\"ov-hero-badge\">池状态</div>
            <div class=\"ov-hero-title\" id=\"ov-hero-title\">分析库存…</div>
            <div class=\"ov-hero-desc\" id=\"ov-hero-desc\">根据缺口 / needs / 可交付 / Cap 自动建议</div>
            <div class=\"ov-hero-warn muted\" id=\"ov-hero-warn\" style=\"display:none;\"></div>
          </div>
          <div class=\"ov-hero-actions\">
            <button type=\"button\" class=\"btn btn-primary\" id=\"ov-hero-btn\" onclick=\"runOverviewRecommended()\">执行推荐</button>
            <button type=\"button\" class=\"btn btn-secondary\" id=\"ov-hero-secondary\" onclick=\"switchPage('ops')\">更多</button>
          </div>
        </div>"""

    ov_new = """            <section class=\"page active\" id=\"page-overview\">
        <div class=\"db-header ov-db-header\">
          <div>
            <div class=\"db-title\">GrokX</div>
            <div class=\"db-subtitle muted\">一屏决策 · 账本 / 缺口 / 任务</div>
          </div>
          <div class=\"ov-header-actions\">
            <span class=\"db-version-tag\" id=\"ov-version\">v-</span>
            <button class=\"btn btn-secondary btn-sm\" onclick=\"refreshOverview()\">刷新</button>
          </div>
        </div>

        <!-- Command deck: single decision surface -->
        <div class=\"ov-hero card ov-hero-deck\" id=\"ov-hero\">
          <div class=\"ov-hero-body\">
            <div class=\"ov-hero-badge\" id=\"ov-hero-badge\">池状态</div>
            <div class=\"ov-hero-title\" id=\"ov-hero-title\">分析库存…</div>
            <div class=\"ov-hero-desc\" id=\"ov-hero-desc\">根据缺口 / needs / 可交付 / Cap 自动建议</div>
            <div class=\"ov-hero-warn muted\" id=\"ov-hero-warn\" style=\"display:none;\"></div>
          </div>
          <div class=\"ov-hero-actions\">
            <button type=\"button\" class=\"btn btn-primary btn-lg\" id=\"ov-hero-btn\" onclick=\"runOverviewRecommended()\">执行推荐</button>
            <button type=\"button\" class=\"btn btn-secondary\" id=\"ov-hero-secondary\" onclick=\"switchPage('ops')\">运维台</button>
          </div>
        </div>"""

    if ov_old not in html:
        raise SystemExit("overview header block not found")
    html = html.replace(ov_old, ov_new, 1)

    html = html.replace(
        '<div class="stats-row stats-row-4" id="ov-kpi-row">',
        '<div class="stats-row stats-row-4 ov-kpi-row" id="ov-kpi-row">',
        1,
    )

    metrics_old = """            <div class=\"ov-live-metrics ov-live-metrics-4\">
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">成功</span>
                <span class=\"ov-live-pill ov-live-ok\" id=\"ov-task-ok-pill\">0</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">失败</span>
                <span class=\"ov-live-pill ov-live-fail\" id=\"ov-task-fail-pill\">0</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">耗时</span>
                <span class=\"ov-live-num\" id=\"ov-task-elapsed\">0s</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">吞吐</span>
                <span class=\"ov-live-num\" id=\"ov-task-thr\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">成功率</span>
                <span class=\"ov-live-num\" id=\"ov-task-rate\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">均耗</span>
                <span class=\"ov-live-num\" id=\"ov-task-avg\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">剩余</span>
                <span class=\"ov-live-num\" id=\"ov-task-eta\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">类型</span>
                <span class=\"ov-live-num\" id=\"ov-task-kind\">—</span>
              </div>
            </div>"""

    metrics_new = """            <div class=\"ov-live-metrics ov-live-metrics-4 ov-live-metrics-primary\">
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">成功</span>
                <span class=\"ov-live-pill ov-live-ok\" id=\"ov-task-ok-pill\">0</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">失败</span>
                <span class=\"ov-live-pill ov-live-fail\" id=\"ov-task-fail-pill\">0</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">耗时</span>
                <span class=\"ov-live-num\" id=\"ov-task-elapsed\">0s</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">吞吐</span>
                <span class=\"ov-live-num\" id=\"ov-task-thr\">—</span>
              </div>
            </div>
            <div class=\"ov-live-metrics ov-live-metrics-4 ov-live-metrics-secondary\">
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">成功率</span>
                <span class=\"ov-live-num\" id=\"ov-task-rate\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">均耗</span>
                <span class=\"ov-live-num\" id=\"ov-task-avg\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">剩余</span>
                <span class=\"ov-live-num\" id=\"ov-task-eta\">—</span>
              </div>
              <div class=\"ov-live-metric\">
                <span class=\"ov-live-label\">类型</span>
                <span class=\"ov-live-num\" id=\"ov-task-kind\">—</span>
              </div>
            </div>"""

    if metrics_old not in html:
        raise SystemExit("metrics block not found")
    html = html.replace(metrics_old, metrics_new, 1)

    html = html.replace(
        '<details class="ov-fold card" id="ov-batch-debug-card" open>',
        '<details class="ov-fold card" id="ov-batch-debug-card">',
        1,
    )

    if html == old:
        raise SystemExit("no html changes")
    html_path.write_text(html, encoding="utf-8")
    print("html ok delta", len(html) - len(old))


POLISH_CSS = r"""
/* —— Homepage 2.5.2 polish —— */
/* Rhythm + hierarchy: Hero → KPI → Task → folds. Less chrome, more air. */

#page-overview {
  max-width: 1080px;
  padding-top: 22px;
  padding-bottom: 48px;
}
#page-overview .ov-db-header {
  margin-bottom: 16px;
  align-items: flex-end;
}
#page-overview .db-title {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.6px;
  line-height: 1.15;
}
#page-overview .db-subtitle {
  margin-top: 4px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.01em;
}
.ov-header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* Hero: soft command strip */
.ov-hero-deck {
  position: relative;
  overflow: hidden;
  padding: 18px 20px;
  margin-bottom: 14px;
  border-radius: 18px;
  border: 1px solid rgba(99, 102, 241, 0.18);
  background:
    radial-gradient(120% 140% at 0% 0%, rgba(99,102,241,0.14), transparent 55%),
    radial-gradient(90% 120% at 100% 0%, rgba(14,165,233,0.10), transparent 50%),
    rgba(255,255,255,0.88);
  box-shadow: 0 8px 28px -12px rgba(79, 70, 229, 0.18);
  backdrop-filter: blur(10px);
}
.ov-hero-deck::after {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  border-radius: 18px 0 0 18px;
  background: linear-gradient(180deg, #6366f1, #38bdf8);
  opacity: 0.9;
}
.ov-hero-deck .ov-hero-body { padding-left: 8px; min-width: 0; flex: 1; }
.ov-hero-deck .ov-hero-badge {
  margin-bottom: 8px;
  font-size: 10px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #4338ca;
  background: rgba(99,102,241,0.12);
}
.ov-hero-deck .ov-hero-title {
  font-size: 18px;
  font-weight: 750;
  letter-spacing: -0.3px;
  margin-bottom: 6px;
  line-height: 1.25;
}
.ov-hero-deck .ov-hero-desc {
  font-size: 12.5px;
  opacity: 0.72;
  max-width: 560px;
  line-height: 1.45;
}
.ov-hero-deck .ov-hero-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.btn-lg {
  min-height: 40px;
  padding: 0 18px;
  font-size: 13.5px;
  font-weight: 650;
  border-radius: 12px;
  box-shadow: 0 6px 16px -6px rgba(37, 99, 235, 0.55);
}
.ov-hero-deck .btn-primary {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  border: none;
}
.ov-hero-deck .btn-primary:hover { filter: brightness(1.05); }
.ov-hero-deck .btn-secondary {
  background: rgba(255,255,255,0.7);
  border: 1px solid rgba(15,23,42,0.08);
}

/* KPI tiles */
#page-overview .ov-kpi-row {
  gap: 10px;
  margin-bottom: 14px;
}
#page-overview .ov-kpi-row .stat-card {
  padding: 14px 14px;
  border-radius: 16px;
  gap: 12px;
  background: rgba(255,255,255,0.9);
  border: 1px solid rgba(15,23,42,0.05);
  box-shadow: 0 4px 14px -8px rgba(15,23,42,0.12);
  transition: border-color .15s, box-shadow .15s, transform .12s;
}
#page-overview .ov-kpi-row .stat-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 22px -10px rgba(15,23,42,0.16);
  border-color: rgba(99,102,241,0.22);
}
#page-overview .ov-kpi-row .stat-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  opacity: 0.92;
}
#page-overview .ov-kpi-row .stat-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted, #94a3b8);
  letter-spacing: 0.02em;
  margin-bottom: 2px;
}
#page-overview .ov-kpi-row .stat-value {
  font-size: 22px;
  font-weight: 750;
  letter-spacing: -0.4px;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
}
#page-overview .ov-kpi-row .stat-sub {
  margin-top: 3px;
  font-size: 11px;
  opacity: 0.7;
}
#page-overview .ov-kpi-row .stat-alert {
  border-color: rgba(239,68,68,0.28);
  background: linear-gradient(180deg, rgba(254,242,242,0.85), rgba(255,255,255,0.92));
}
#page-overview .ov-kpi-row .stat-ok {
  border-color: rgba(34,197,94,0.28);
}
#page-overview .ov-kpi-row .stat-warn {
  border-color: rgba(245,158,11,0.35);
}

/* Task stage: single surface */
#page-overview .ov-live-card {
  margin-top: 0;
  padding: 16px 18px 16px;
  border-radius: 16px;
  border: 1px solid rgba(15,23,42,0.06);
  background: rgba(255,255,255,0.94);
  box-shadow: 0 6px 20px -12px rgba(15,23,42,0.14);
}
#page-overview .ov-live-head { margin-bottom: 12px; }
#page-overview .ov-live-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text, #0f172a);
  letter-spacing: -0.1px;
}
#page-overview .ov-live-kind {
  font-size: 12px;
  color: var(--text-muted, #94a3b8);
}
#page-overview .ov-live-panel {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
}
#page-overview .ov-live-progress-row { margin-bottom: 8px; }
#page-overview .ov-live-val {
  font-size: 15px;
  font-weight: 750;
  letter-spacing: -0.2px;
}
#page-overview .ov-live-bar {
  height: 8px;
  margin-bottom: 14px;
  background: rgba(15,23,42,0.06);
  border: none;
  box-shadow: inset 0 1px 2px rgba(15,23,42,0.04);
}
#page-overview .ov-live-bar .progress-bar {
  background: linear-gradient(90deg, #3b82f6 0%, #22d3ee 100%);
  box-shadow: 0 0 12px rgba(59,130,246,0.35);
}

#page-overview .ov-live-metrics-primary {
  gap: 8px;
  margin-bottom: 8px;
}
#page-overview .ov-live-metrics-primary .ov-live-metric {
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 6px;
  min-height: 56px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15,23,42,0.03);
  border: 1px solid rgba(15,23,42,0.04);
}
#page-overview .ov-live-metrics-primary .ov-live-label {
  font-size: 11px;
  font-weight: 600;
  opacity: 0.7;
}
#page-overview .ov-live-metrics-primary .ov-live-num {
  font-size: 16px;
  font-weight: 750;
}
#page-overview .ov-live-metrics-primary .ov-live-pill {
  min-width: 36px;
  height: 26px;
  font-size: 13px;
  border-radius: 8px;
}

#page-overview .ov-live-metrics-secondary {
  gap: 0;
  padding: 8px 4px 2px;
  border-top: 1px dashed rgba(15,23,42,0.08);
  margin-top: 2px;
}
#page-overview .ov-live-metrics-secondary .ov-live-metric {
  min-height: 28px;
  padding: 4px 6px;
}
#page-overview .ov-live-metrics-secondary .ov-live-label { font-size: 11px; }
#page-overview .ov-live-metrics-secondary .ov-live-num {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
}
#page-overview .ov-live-msg {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(15,23,42,0.05);
  font-size: 12.5px;
  color: var(--text-secondary, #64748b);
}

#page-overview .badge-running {
  box-shadow: 0 0 0 0 rgba(34,197,94,0.35);
  animation: ov-pulse 1.8s ease-out infinite;
}
@keyframes ov-pulse {
  0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.35); }
  70% { box-shadow: 0 0 0 8px rgba(34,197,94,0); }
  100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
}

#page-overview .ov-more {
  margin-top: 12px;
  gap: 8px;
}
#page-overview .ov-fold {
  border-radius: 14px;
  border: 1px solid rgba(15,23,42,0.05);
  background: rgba(255,255,255,0.78);
  box-shadow: none;
}
#page-overview .ov-fold > summary {
  padding: 11px 14px;
  font-size: 12.5px;
  color: var(--text-secondary, #475569);
}
#page-overview .ov-fold > summary:hover {
  background: rgba(15,23,42,0.02);
}
#page-overview .ov-fold-summary {
  color: var(--text-muted, #94a3b8);
  font-variant-numeric: tabular-nums;
}
#page-overview .ov-fold .stat-card {
  box-shadow: none;
  border-radius: 12px;
  padding: 12px 14px;
  background: rgba(15,23,42,0.025);
}
#page-overview .table-compact th {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted, #94a3b8);
  border-bottom: 1px solid rgba(15,23,42,0.06);
}
#page-overview .table-compact td {
  border-bottom: 1px solid rgba(15,23,42,0.04);
}
#page-overview .table-compact tr:last-child td { border-bottom: none; }

[data-theme="dark"] .ov-hero-deck {
  background:
    radial-gradient(120% 140% at 0% 0%, rgba(99,102,241,0.22), transparent 55%),
    radial-gradient(90% 120% at 100% 0%, rgba(14,165,233,0.12), transparent 50%),
    rgba(30,32,40,0.92);
  border-color: rgba(129,140,248,0.22);
}
[data-theme="dark"] #page-overview .ov-kpi-row .stat-card,
[data-theme="dark"] #page-overview .ov-live-card,
[data-theme="dark"] #page-overview .ov-fold {
  background: rgba(30,32,40,0.88);
  border-color: rgba(255,255,255,0.06);
}
[data-theme="dark"] #page-overview .ov-live-metrics-primary .ov-live-metric {
  background: rgba(255,255,255,0.04);
  border-color: rgba(255,255,255,0.05);
}
[data-theme="dark"] .ov-hero-deck .btn-secondary {
  background: rgba(255,255,255,0.06);
  border-color: rgba(255,255,255,0.08);
}

@media (max-width: 720px) {
  #page-overview .ov-hero-deck {
    flex-direction: column;
    align-items: stretch;
  }
  #page-overview .ov-hero-actions { width: 100%; }
  #page-overview .ov-hero-actions .btn { flex: 1; justify-content: center; }
}
"""


def polish_css() -> None:
    css_path = ROOT / "client" / "static" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    marker = "/* —— Homepage 2.5.2 polish —— */"
    if marker in css:
        css = css[: css.find(marker)].rstrip() + "\n"
    css_path.write_text(css.rstrip() + "\n" + POLISH_CSS, encoding="utf-8")
    print("css ok", css_path.stat().st_size)


def polish_tests() -> None:
    test_path = ROOT / "tests" / "test_client_homepage_24.py"
    t = test_path.read_text(encoding="utf-8")
    if "2.5.2 polish" in t:
        print("test already")
        return
    add = """


def test_homepage_252_polish_markers():
    html = Path("client/static/index.html").read_text(encoding="utf-8")
    css = Path("client/static/style.css").read_text(encoding="utf-8")
    assert "ov-hero-deck" in html
    assert "ov-live-metrics-primary" in html
    assert "ov-live-metrics-secondary" in html
    assert "db-subtitle" in html
    assert "Homepage 2.5.2 polish" in css
    assert "ov-live-start" not in html
"""
    test_path.write_text(t.rstrip() + add + "\n", encoding="utf-8")
    print("test updated")


if __name__ == "__main__":
    polish_html()
    polish_css()
    polish_tests()
    print("done")
