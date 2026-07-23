# -*- coding: utf-8 -*-
"""Build client/static/app.js IIFE from js/core.js + js/main.js.

Source of edit: modules. Runtime + tests: app.js bundle.
  python scripts/build_grokx_app_bundle.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "client" / "static"


def strip_exports(src: str) -> str:
    """Remove ES module export keywords without touching identifiers like cpa_export."""
    # export async function / export function / export var / export const / export let
    src = re.sub(r"^export\s+async\s+function\s+", "async function ", src, flags=re.M)
    src = re.sub(r"^export\s+function\s+", "function ", src, flags=re.M)
    src = re.sub(r"^export\s+var\s+", "var ", src, flags=re.M)
    src = re.sub(r"^export\s+const\s+", "const ", src, flags=re.M)
    src = re.sub(r"^export\s+let\s+", "let ", src, flags=re.M)
    # export { ... } — not used, but be safe
    src = re.sub(r"^export\s*\{[^}]*\}\s*;?\s*$", "", src, flags=re.M)
    return src


def main() -> int:
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    main_js = (STATIC / "js" / "main.js").read_text(encoding="utf-8")

    core_body = strip_exports(core)
    core_body = re.sub(
        r"if \(typeof window !== \"undefined\"\) \{[\s\S]*?window\.GrokX\.toast = toast;\n\}\n?",
        "",
        core_body,
    )

    main_body = main_js
    main_body = re.sub(
        r'^/\*\* GrokX SPA[\s\S]*?from "\./core\.js";\n\n',
        "",
        main_body,
        count=1,
    )
    main_body = re.sub(
        r"^const GrokXParse =[\s\S]*?: null;\n\n",
        'var GrokXParse = typeof window !== "undefined" ? window.GrokXParse : null;\n\n',
        main_body,
        count=1,
    )
    main_body = strip_exports(main_body)

    # Safety: never leave bare "export " (would break IIFE)
    if re.search(r"(?m)^export\s", main_body) or re.search(r"(?m)^export\s", core_body):
        raise SystemExit("export keyword remains after strip")
    # Regression: cpa_export must not become cpa_
    if "st.||" in main_body or "cpa_" + "export" not in main_js:
        pass
    if "st.export" not in main_body and "st.export" in main_js:
        raise SystemExit("st.export corrupted during build")
    if "st.||" in main_body:
        raise SystemExit("st.|| corruption")

    bundle = f"""/* GrokX frontend v1.8.0 — IIFE bundle (source: js/core.js + js/main.js) */
/* Rebuild: python scripts/build_grokx_app_bundle.py */
(function () {{
  "use strict";

{core_body}

{main_body}

  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", function () {{ bootGrokX(); }});
  }} else {{
    bootGrokX();
  }}
}})();
"""
    out = STATIC / "app.js"
    out.write_text(bundle, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        return r.returncode
    print(f"wrote {out} ({len(bundle)} bytes) node --check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
