# Export 插件契约（用户架构）

```text
SSO ──mint──► RT/AT ──入账──► auth.json ──工厂──► export packs
```

- **mint 默认不写 cpa_files / cockpit**
- **auth.json 不依赖 cpa pack**（直接 upsert entry）
- **cpa_files / sub2api / cockpit 同级 pack**；xAI 文件格式实现 `backends/export/xai_pack`；Cockpit 账号格式实现 `backends/export/cockpit`；仅 `--export` 或显式 packs 时写出

---

## 1. 分层

| 阶段 | 输入 | 输出 |
|------|------|------|
| mint | SSO | RT/AT → auth.json |
| 工厂 | auth.json | cpa_files / sub2api / cockpit / … |
| ops | pack 文件 | 远端 upload / UI 导入 |

```text
mint
  → publish_credentials(payload, packs=[])   # 默认只入账
       └─ auth.json

需要时：
  --export cpa_files   → cpa_export/xai-*.json
  --export sub2api     → sub2api_export/grok-*.json
  --export cockpit     → cockpit_export/grok-*.json + grok_accounts.json
  --mint-write-cpa     → mint 时可选顺带 cpa pack

ops（非 export 插件）：
  --cpa-upload         → CLIProxy management API
  --sub2api-upload     → POST /api/v1/admin/accounts/data（admin Bearer）
  # cockpit 暂无远程 upload；本地导入 Cockpit Tools

旁路互转（已有 pack 文件，不经 auth.json）：
  scripts/convert_cpa_sub2api.py cpa-to-sub | sub-to-cpa | auto
  实现：grokreg.backends.export.convert
  禁止：mint / refresh / upload / 改 auth.json
```

---

## 2. API

```python
# 默认：只入账
publish_credentials(payload, auth_path="auth.json", packs=None)

# 可选顺带 pack
publish_credentials(payload, packs=["cpa_files"])
publish_credentials(payload, packs=["cockpit"])

# 批出包
export_auth_pool("sub2api", "auth.json")
export_auth_pool("cpa_files", "auth.json")
export_auth_pool("cockpit", "auth.json")
```

---

## 3. CLI

```bash
python main.py --mint email@x.com              # → auth.json only
python main.py --mint all --mint-missing       # 池里没有的才 mint
python main.py --export sub2api --export-only email@x.com
python main.py --export cpa_files --export-only email@x.com
python main.py --export cpa                    # ≡ cpa_files / cliproxy
python main.py --export cockpit --export-only email@x.com
python main.py --export cockpit                # → cockpit_export/
python main.py --mint email --mint-write-cpa   # 可选：mint 时写 cpa

# 已有 pack 互转（不读 auth.json）
python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o sub2api_export --dry-run --limit 5
python scripts/convert_cpa_sub2api.py sub-to-cpa sub2api_export -o cpa_export
python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o sub2api_export --merge
```

Python：

```python
from grokreg.backends.export import (
    convert_paths,
    cpa_to_sub2api_document,
    detect_kind,
    sub2api_to_cpa_payloads,
    entry_to_cockpit_account,
)
```

---

## 4. 加新 pack

1. `export_entry` + `kind=pack`  
2. `PACK_BACKENDS`  
3. `--export foo`  

### 4.1 cockpit（已实现）

| 项 | 值 |
|----|-----|
| 模块 | `backends/export/cockpit.py` |
| 别名 | `cockpit` / `cp` / `cockpit_tools` / `antigravity_cockpit` |
| 目录 | `cockpit_export/` |
| 单号文件 | `grok-<email>.json`（单对象） |
| 批索引 | `grok_accounts.json`（数组，按 email upsert） |
| 必要字段 | `refresh_token` + `access_token`/`email`/`expires_at`(unix) + OIDC 三件套 |
| 可选镜像 | `auth_raw`、`subscription_raw` |
| 不做 | 远端 upload；mint 默认写出 |

---

## 5. 一句话

> **取证入账；出包按需；cpa / sub2api / cockpit 同级且默认都不自动写。**
