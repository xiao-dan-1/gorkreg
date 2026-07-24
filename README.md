# gorkreg

Grok / xAI **纯协议**注册与 OAuth 凭证运维命令行工具。

本仓库提供 CLI，用于：

1. 以 HTTP 协议完成账号注册（不以浏览器自动化为主路径）
2. 取得 SSO，经 device-code 流程 mint OAuth 令牌
3. 维护本地凭证账本（`auth.json`）
4. 刷新令牌、探活，以及按需导出 / 上传 pack

**仓库名：** `gorkreg`  
**Python 包名：** `grokreg`（`import grokreg`）

---

## 公开树范围

| 包含 | 不包含 |
|------|--------|
| 协议 CLI（`main.py`、`grokreg/`） | Web 控制台（`client/`） |
| 产线辅助脚本（`scripts/`） | 完整运维手册（`docs/`） |
| 离线单元 / 契约测试（`tests/`） | 第三方对照切片（`ref/`） |
| 环境与配置样例 | 密钥、花名册、线上账本 |

运行时产物（`.env`、`auth.json`、`sso_roster.txt`、`output/`、export 目录等）已在 `.gitignore` 中，**禁止提交**。

---

## 数据流

```text
邮箱 / CloudMail
        │
        ▼
     注册  ──►  sso_roster.txt     # SSO 索引
        │
        ▼
 device-code mint ──►  auth.json   # 凭证真源
        │
        ├── refresh / remint
        ├── probe-quota
        └── export ──► cpa_export | sub2api_export | cockpit_export
                              └── 可选远端上传
```

**账本优先：** mint、refresh、probe、`--summary` **只读写 `auth.json`**。  
export 产物是派生包，不是凭证仓库。

---

## 环境要求

| 项 | 说明 |
|----|------|
| Python | 建议 3.11+ |
| 本机代理 | 混合口，供嵌套 CONNECT（默认 `127.0.0.1:7890`） |
| 动态住宅 | `PROXY_DYNAMIC_TEMPLATE`（每号随机 SID） |
| 打码 | CapSolver / YesCaptcha / 2Captcha |
| 可选 | CloudMail 管理端；CLIProxy / sub2api 上传凭据 |

协议主路径依赖：

```text
curl_cffi、PyYAML
```

浏览器打码见 `requirements-browser.txt`，**非日常必需**。

---

## 安装

```bash
git clone git@github.com:xiao-dan-1/gorkreg.git
cd gorkreg
python -m pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

Windows 可用 `copy` 代替 `cp`。

正式跑之前先配好 `.env`。密钥尽量只写 `.env`；`config.yaml` 可空壳。  
非空环境变量 **仅内存覆盖** YAML，**永不写回文件**。

### 最小 `.env` 示例

```env
CAPTCHA_BACKEND=capsolver
CAPSOLVER_API_KEY=

LOCAL_PROXY=http://127.0.0.1:7890
PROXY_DYNAMIC_TEMPLATE=user-region-US-sid-xxxxxxxx-t-5:password@host:port

# CloudMail 批产需要
CLOUDMAIL_URL=
CLOUDMAIL_ADMIN_EMAIL=
CLOUDMAIL_PASSWORD=
CLOUDMAIL_DOMAINS=

# 可选上传
# CPA_BASE_URL=
# CPA_SECRET_KEY=
# SUB2API_BASE_URL=
# SUB2API_ADMIN_EMAIL=
# SUB2API_ADMIN_PASSWORD=
```

---

## 代理模型

```text
应用（curl_cffi）
    → LOCAL_PROXY（本机 hop，如 Clash :7890）
        → 动态住宅上游（CONNECT）
            → accounts.x.ai / auth.x.ai / …
```

| 流量 | 代理 |
|------|------|
| 注册、mint、probe | `LOCAL_PROXY`（同一 hop1） |
| 收码（Graph / IMAP） | 默认直连 |
| 批产 preflight | 嵌套 CONNECT + 全链探测；失败则 **0 烧号退出** |

**不要**用常见 v2rayN HTTP 口（如 `10808`）作 `chain_via`：第二层 CONNECT 容易黑洞。  
注册不依赖系统级 `HTTP_PROXY` / `HTTPS_PROXY`。

自检：

```bash
python main.py --env-check
python main.py --check-chain
```

期望：`ready_for_register: yes`、`ready_for_mint: yes`、`check-chain: OK`。

---

## 运维命令

均在仓库根目录执行。

### 环境与链路

```bash
python main.py --env-check
python main.py --check-chain
python main.py --check-proxy --check-proxy-times 3
```

### 注册

```bash
# CloudMail 批产（推荐）
CAPTCHA_BACKEND=capsolver \
  python scripts/prod_cloudmail_batch.py -n 20 -j 6 --account-timeout 90 --ascii-log

# CloudMail 单号
python main.py --register-cloudmail -v

# 名单文件（如 Outlook：email----password----client_id----refresh_token）
python main.py --batch mails.txt --region US -j 2 --ascii-log
```

成功会向 `sso_roster.txt` 追加：

```text
email----password----sso
```

**并发建议**

| 场景 | `-j` |
|------|------|
| 稳产 | 2～4 |
| 冲量 | 6～8 |
| 长跑（数百～千号） | 优先 6～8；**勿默认 j=12** |

主要 KPI：成功率（ok%）与成功号耗时。总 thr 易被 1～2 个慢号拖死。

### Mint（入账）

```bash
python main.py --mint all --mint-missing --no-probe -j 4 --ascii-log
python main.py --mint all --mint-missing --limit 10 --no-probe -j 2 --ascii-log
```

- `--mint-missing`：只处理花名册有、账本无的邮箱  
- `--limit`：过滤后再截断；mint-missing 时 **优先最新 SSO**  
- 默认 mint **只写 `auth.json`**，不写 CPA 文件  

### 状态、探活、刷新

```bash
python main.py --summary
python main.py --auth-status all --needs-refresh-only

python main.py --probe-quota all --probe-mode models -j 6
python main.py --probe-quota EMAIL --probe-mode billing
python main.py --probe-quota EMAIL --probe-mode chat

python main.py --refresh all --needs-refresh-only --remint-on-revoke --no-probe -j 8
```

| mode | 用途 |
|------|------|
| `models` | 令牌是否存活（快） |
| `billing` | xAI 账期 |
| `chat` / `quota` | 响应头上的用量类限制 |

Access 约 **6 小时**。Refresh token 被吊销后须带 SSO remint，勿单独死磕 refresh。

### 导出与上传

```bash
python main.py --export cpa
python main.py --export sub2api
python main.py --export cockpit

python main.py --cpa-upload all --cpa-missing -j 20
python main.py --sub2api-upload all --sub2api-on-exists overwrite
```

上传地址与密钥来自环境变量。**未经明确授权，勿对生产目标乱传。**

### 花名册巡检

```bash
python main.py --sso-audit
python main.py --recover-sso-roster --recover-dry-run
python main.py --recover-sso-roster
```

| 巡检信号 | 常见处理 |
|----------|----------|
| `in_cli_not_auth` | `--mint all --mint-missing` |
| `in_output_not_cli` | `--recover-sso-roster` |

---

## 打码后端

| `CAPTCHA_BACKEND` | 行为 |
|-------------------|------|
| `capsolver` | 仅 CapSolver（`AntiTurnstileTaskProxyLess`） |
| `yescaptcha` | 仅 YesCaptcha |
| `twocaptcha` | 仅 2Captcha |
| `auto` | Yes → Cap → 2C（余额 soft-skip） |

优先级：**CLI 参数 > 环境变量 > config.yaml > auto**。

付费前会查余额；零余额会熔断批产。prefetch 超时时优先等待已付费任务，降低双付风险。

---

## 目录结构

```text
main.py                 CLI 入口
grokreg/                协议核心、OAuth、backends、ops
  pipeline/             注册流水线
  oauth/                device-code mint、refresh、probe
  backends/             mail / captcha / export 工厂
  ops/                  命令实现
scripts/                批产与维护脚本
tests/                  离线测试（不访问外网）
.env.example
config.example.yaml
Dockerfile
docker-compose.yml
.github/workflows/      CI
```

---

## 开发

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

测试以离线契约为主。完整参数：

```bash
python main.py --help
```

---

## 安全

- 勿提交：`.env`、`auth.json`、`sso_roster.txt`、refresh token、管理端密钥  
- `sso_roster.txt` 可能含密码字段，按敏感文件保管  
- 勿在 Issue 或聊天中粘贴凭证  

---

## 许可

本项目以 [MIT License](LICENSE) 发布。

---

## 免责声明

本软件会访问由第三方控制的服务。使用者须自行遵守相关服务条款、当地法律，以及运维安全（限流、打码费用、代理策略等）。维护者按「现状」提供代码，不附带任何担保。
