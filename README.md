# gorkreg · Grok 纯协议注册

纯协议（无浏览器主路径）产号 → SSO → device-code mint → `auth.json` 账本 → 按需 export/upload。  
**Python 包目录仍为 `grokreg/`**（import 名不变）；本仓库公开名 **gorkreg**。

**日常：分步运维**（产号 → mint → probe/refresh → 按需 export/upload）。  
密钥只放 **`.env`**，勿提交。细节见 [docs/开发手册.md](docs/开发手册.md)、分层见 [docs/架构债.md](docs/架构债.md)。

> 本公开树 **不含** 本地 Web 控制台（GrokX / `client/`）。CLI 为唯一公开入口。

---

## 介绍

| 阶段 | 做什么 | 真源 |
|------|--------|------|
| 注册 | CloudMail 批产，拿 SSO | **SSO 花名册** `sso_roster.txt`（mint 索引）+ `output/` 证据 |
| mint | SSO → RT/AT | **`auth.json` 账本** |
| probe / refresh | 探活 / 刷 AT | **`auth.json` only** |
| export | 出 CPA / sub2api / Cockpit 包 | `cpa_export/` · `sub2api_export/` · `cockpit_export/` |
| upload | 推远端 | CLIProxy / sub2api |

**分层：** mint / remint / refresh / probe **不依赖** `cpa_export`；xAI / Cockpit pack 文件只在 export/upload。

> 导出：日常 `python main.py --export cpa`（≡ `cpa_files` / `cliproxy` 别名）。目录 `cpa_export/`。上传 `--cpa-upload`。env：`CPA_BASE_URL` / `CPA_SECRET_KEY` / `CPA_AUTH_DIR`。
> Cockpit Tools：`python main.py --export cockpit` → `cockpit_export/grok-*.json` + `grok_accounts.json`。
> pack 实现：`grokreg.backends.export.xai_pack` / `cockpit`（非 OAuth 协议模块）。

| 配置要点 | 值 |
|----------|-----|
| 注册 hop1 | `LOCAL_PROXY=http://127.0.0.1:7890` |
| 动态住宅 | `PROXY_DYNAMIC_TEMPLATE=...`（可无 `http://`） |
| 打码 | `YESCAPTCHA_API_KEY` |

---

## 配置

```bash
cd /path/to/gorkreg
python -m pip install -r requirements.txt
# 可选浏览器打码（非日常）: pip install -r requirements-browser.txt && playwright install chromium
cp config.example.yaml config.yaml   # Windows: copy config.example.yaml config.yaml
cp .env.example .env
```

**.env 最少：**

```env
YESCAPTCHA_API_KEY=...
LOCAL_PROXY=http://127.0.0.1:7890
# PROXY_DYNAMIC_TEMPLATE=USER-region-US-sid-XXXX-t-5:PASS@host:2000

CLOUDMAIL_URL=...
CLOUDMAIL_ADMIN_EMAIL=...
CLOUDMAIL_PASSWORD=...
CLOUDMAIL_DOMAINS=...

# CLIProxy 上传时
# CPA_BASE_URL=...
# CPA_SECRET_KEY=...
```

一般**不要**设 `HTTPS_PROXY=10808`。密钥用 `.env`，yaml 可留空壳。

---

## 自检

```bash
python main.py --env-check
python main.py --check-chain
```

期望：`ready_for_register: yes`；proxy **7890**；链检查 hop1=7890。

---

## 日常分步运维

```bash
# 1) 产号（仅注册；默认 proxy 类失败多试 1 次）
python scripts/prod_cloudmail_batch.py -n 4 -j 2 --ascii-log
# 关闭重试: --proxy-retries 0

# 2) 入账（缺的才 mint；--limit 优先最新 SSO；mint 易限流，建议 -j 1）
python main.py --mint all --mint-missing --limit 4 --no-probe -j 1 --ascii-log

# 3) 探活（读 auth.json，不依赖 pack 文件；单邮箱，勿逗号列表）
python main.py --probe-quota EMAIL --probe-mode models
# 判 free 是否用完（看 class=quota_exhausted；裸 429≠用尽）→ 详 docs/开发手册.md 步骤7
# python main.py --probe-quota EMAIL --probe-mode chat
# python main.py --probe-quota all --probe-mode chat -j 2 --limit 30

# 4) 保活
python main.py --refresh all --needs-refresh-only --remint-on-revoke --no-probe -j 8

# 5) 按需出包 / 上传（--export cpa ≡ cpa_files；目录 cpa_export/）
python main.py --export cpa
python main.py --export cockpit --export-only EMAIL   # Cockpit Tools 本地导入
python main.py --cpa-upload all --cpa-missing -j 20
```

| 参数 | 含义 |
|------|------|
| `-n` / `-j` | 批产个数 / 并发 |
| `--ascii-log` | cmd 友好图标 |
| `--proxy-retries N` | 批产网络/proxy 失败额外重试（默认 1） |
| `--mint-missing` | 只 mint 账本没有的号 |
| `--limit N` | mint/refresh/probe 等截断；mint 时 **最新优先** |
| `--dry-run` | 批产只演日志 |

测日志（不烧号）：

```bash
python scripts/prod_cloudmail_batch.py -n 4 -j 2 --ascii-log --dry-run
```

---

## 号池维护

> `sso_roster.txt` = **SSO 花名册 / mint 索引**（`email----password----sso`（含密码，供登录/核对））。`auth.json` 不存 SSO；`output/` 为注册证据（可滚动，非全量保证）。三落点不合并；产号后 `recover → mint-missing → sso-audit`。
>
> 2 字段旧行用 `python main.py --migrate-sso-roster` 补密码（从 output/accounts 证据）。
> account 证据目录：`output/accounts/`（`python main.py --migrate-account-evidence` 从根目录迁入）。


```bash
# SSO 丢失时从 output/ 回填 sso_roster（缺的才 append；output 仅滚动证据）
python main.py --recover-sso-roster --recover-dry-run
python main.py --recover-sso-roster
python main.py --sso-audit

python main.py --auth-status all --needs-refresh-only --limit 20 --ascii-log
python main.py --refresh all --needs-refresh-only --remint-on-revoke --no-probe -j 8
python main.py --probe-quota all --probe-mode models -j 8 --limit 20
```

---

## Pack 互转（CPA ↔ sub2api，旁路）

已有 `cpa_export/` 或 `sub2api_export/` 文件时，**不 mint / 不 upload / 不改 auth.json**：

```bash
# CPA → sub2api（一号一信封；--merge 合并导入包）
python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o sub2api_export --dry-run --limit 5
python scripts/convert_cpa_sub2api.py cpa-to-sub cpa_export -o sub2api_export

# sub2api → CPA
python scripts/convert_cpa_sub2api.py sub-to-cpa sub2api_export -o cpa_export --dry-run --limit 5
```

契约见 [docs/export-plugin-contract.md](docs/export-plugin-contract.md)。

---

## 开发自检（pytest）

```bash
python -m pip install -r requirements.txt
python -m pytest
# 或:
python -m pytest tests/test_ledger_export_boundary.py tests/test_refresh_ledger_regression.py
```

`pytest.ini` 已固定 `testpaths=tests`。契约见 [docs/架构债.md](docs/架构债.md)。**不连外网。**

---

## 说明

- Access ≈ **6h**，靠 refresh；RT revoke → remint（需 SSO）。  
- 进度行以 `#` 开头；默认安静批产 + 静态完成行。  
- 密钥不入库、不贴聊天。  
- 可选实验入口 `scripts/run.py` / pipeline_* 仍在树内，**日常以分步为准**。  

更多排错：[docs/开发手册.md](docs/开发手册.md)。
