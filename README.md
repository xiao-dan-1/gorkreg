# gorkreg

**Grok / xAI 纯协议注册与凭证运维 CLI**

不依赖浏览器主路径：注册拿 SSO → device-code mint 入账 → refresh / probe → 按需 export / upload。  
仓库名 **gorkreg**；Python 包目录 **`grokreg/`**（`import grokreg`）。

本公开树只含协议 CLI、离线测试与样例配置。  
**不含** Web 控制台、详细运维手册、对照开源切片、密钥与号池数据。

---

## 流程

```text
邮箱/CloudMail
    → 注册（scrape → 验证码 → Turnstile → create → SSO）
    → sso_roster.txt          # mint 索引
    → mint（device-code OAuth）
    → auth.json               # 凭证真源
    → refresh / probe
    → 可选 --export cpa|sub2api|cockpit → 上传
```

| 阶段 | 真源 | 说明 |
|------|------|------|
| 注册成功 | `sso_roster.txt` + `output/accounts/` | 有 SSO 才算过 |
| 凭证 | **`auth.json` only** | mint / refresh / probe / summary 只认账本 |
| 出包 | `cpa_export/` 等 | **按需** export；不进协议主路径 |

---

## 要求

- Python 3.11+
- 本机代理（Clash 等）作 hop1，默认 `127.0.0.1:7890`（可用 `LOCAL_PROXY` 改）
- 动态住宅代理模板（链式：本机 hop1 → 上游）
- 打码：CapSolver / YesCaptcha / 2Captcha（`CAPTCHA_BACKEND`）
- 可选：CloudMail 自建域、CLIProxy / sub2api 上传凭据

密钥只写 **`.env`**，勿提交。

---

## 安装

```bash
git clone git@github.com:xiao-dan-1/gorkreg.git
cd gorkreg
python -m pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml   # Windows: copy ...
```

编辑 `.env`（最少）：

```env
CAPTCHA_BACKEND=capsolver
CAPSOLVER_API_KEY=...

LOCAL_PROXY=http://127.0.0.1:7890
PROXY_DYNAMIC_TEMPLATE=USER-region-US-sid-XXXX-t-5:PASS@host:port

# CloudMail 批产时
CLOUDMAIL_URL=...
CLOUDMAIL_ADMIN_EMAIL=...
CLOUDMAIL_PASSWORD=...
CLOUDMAIL_DOMAINS=...
```

`config.yaml` 可空壳；非空 env **只覆盖内存，不写回文件**。

---

## 自检

```bash
python main.py --env-check
python main.py --check-chain
```

期望：

- `ready_for_register: yes`（打码有余额）
- `ready_for_mint: yes`（`mint.proxy` = `LOCAL_PROXY`）
- `check-chain: OK`，hop1 为本机代理口，嵌套 CONNECT 通

**不要**用 `10808`（常见 v2rayN）当注册链 `chain_via`：第二层 CONNECT 易黑洞。  
系统 `HTTP(S)_PROXY` 默认不必设。

---

## 日常命令

### 1. 产号（SSO）

```bash
# CloudMail 批产（推荐）
CAPTCHA_BACKEND=capsolver python scripts/prod_cloudmail_batch.py -n 20 -j 6 --account-timeout 90 --ascii-log

# 单号
python main.py --register-cloudmail -v

# 名单文件（Outlook 四段线等）
python main.py --batch mails.txt --region US -j 2 --ascii-log
```

| 建议 | 说明 |
|------|------|
| 稳产 | `j=2～4` |
| 冲量 | `j=6～8` |
| 千号长跑 | **不要默认 j=12** |
| KPI | ok% + 成功号耗时；总 thr 易被 1～2 慢号拖死 |

成功会 append `sso_roster.txt`（`email----password----sso`）。

### 2. Mint（入账）

```bash
# 只 mint 账本还没有的号（roster 有、auth 无）
python main.py --mint all --mint-missing --no-probe -j 4 --ascii-log

# 调试截断：最新 SSO 优先
python main.py --mint all --mint-missing --limit 10 --no-probe -j 2 --ascii-log
```

默认 **只写 `auth.json`**，不写 CPA 文件。要 pack：`--export` 或 `--mint-write-cpa`。

### 3. 探活 / 保活

```bash
python main.py --summary
python main.py --auth-status all --needs-refresh-only

python main.py --probe-quota all --probe-mode models -j 6
python main.py --probe-quota EMAIL --probe-mode billing   # xAI 账期
python main.py --probe-quota EMAIL --probe-mode chat      # 额度头

python main.py --refresh all --needs-refresh-only --remint-on-revoke --no-probe -j 8
```

- AT ≈ **6h**；RT 吊销 → remint（需 SSO），勿死磕 refresh  
- `models` 快检；`chat`/`quota` 才看额度语义  

### 4. 导出 / 上传（可选）

```bash
python main.py --export cpa              # → cpa_export/（≡ cpa_files）
python main.py --export sub2api
python main.py --export cockpit

python main.py --cpa-upload all --cpa-missing -j 20
python main.py --sub2api-upload all --sub2api-on-exists overwrite
```

上传目标与密钥在 `.env`（`CPA_*` / `SUB2API_*`）。**未授权勿对生产乱传。**

### 5. 号池巡检

```bash
python main.py --sso-audit
# in_cli_not_auth → --mint all --mint-missing
# output 有 SSO、roster 缺 → --recover-sso-roster（先 --recover-dry-run）
```

---

## 代理架构（简）

```text
curl_cffi → 127.0.0.1:LOCAL_PROXY
         → CONNECT → 动态住宅（PROXY_DYNAMIC_TEMPLATE，每号随机 SID）
```

- 注册 / mint / probe：同一 hop1（`LOCAL_PROXY`，默认 7890）  
- 收码（Graph/IMAP）：默认 **直连**  
- 批产默认 preflight：链不通则 **0 烧号退出**  

临时换 mint 出口：`--mint-proxy URL`（不读已废弃的 `MINT_PROXY`）。

---

## 打码

| `CAPTCHA_BACKEND` | 行为 |
|-------------------|------|
| `capsolver` | 仅 CapSolver（`AntiTurnstileTaskProxyLess`） |
| `yescaptcha` | 仅 Yes |
| `twocaptcha` | 仅 2C |
| `auto` | Yes → Cap → 2C（余额 soft-skip） |

优先级：**CLI > env > config > auto**。  
空余额熔断；prefetch 超时会等已付费任务，降低双付。

---

## 项目结构（公开）

```text
main.py                 # 入口 → grokreg.cli
grokreg/                # 协议 + ops + backends
  pipeline/             # register_one
  oauth/                # mint / refresh / probe
  backends/             # mail · captcha · export
  ops/                  # CLI 命令实现
scripts/
  prod_cloudmail_batch.py
  recover_sso_failed.py
  convert_cpa_sub2api.py
  ...
tests/                  # 离线契约测试（不连外网）
.env.example
config.example.yaml
Dockerfile
```

运行时本地生成（**gitignore**）：`.env`、`config.yaml`、`auth.json`、`sso_roster.txt`、`output/`、`cpa_export/` 等。

---

## 测试

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

CI：`.github/workflows/pytest.yml`。

---

## 安全

- 永不提交：`.env`、`auth.json`、`sso_roster.txt`、邮箱 RT、CPA secret  
- `sso_roster` 含密码字段，仅本地保管  
- Issue / 聊天勿贴 token  

---

## 许可

[MIT](LICENSE)

---

## 说明

- 包名 `grokreg` 与仓库名 `gorkreg` 并存属有意区分，import 勿改成 gorkreg。  
- CLI 完整参数：`python main.py --help`。  
- 更细的产线手册与对照代码不在本仓；以代码与本 README 为准。  
