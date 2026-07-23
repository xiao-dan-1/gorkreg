# 效率研究操作卡（Efficiency Playbook）

> 对应计划：`.hermes/plans/2026-07-20_013046-script-efficiency-research.md`  
> 原则：**先测量再改码；禁止盲加 j；ok% 与 wall 并列。**

## 1. 台账入口

| 命令 | 作用 |
|------|------|
| `python main.py --bench-show` | 看最近注册耗时摘要 |
| `python main.py --bench-backfill` | 从 `output/*batch*.log` 回填 |
| `python scripts/prod_cloudmail_batch.py …` | CloudMail 产线批；**结束后自动写 bench** |

产物：

```text
output/bench_runs.jsonl
output/bench_summary.md
output/prod_cloudmail/batch_*_j*n*.{json,md}
output/efficiency/          # 对照实验笔记（自建）
```

## 2. 测量前预检

```bash
python main.py --check-chain
python main.py --env-check
# 打码：Yes 或 CapSolver 有余额（auto 优先 Yes）
```

- 注册 hop1：**7890**  
- mint/probe 外网：**10808**（`MINT_PROXY` / `PROBE_PROXY`）  
- 打码无余额：**勿烧号**

## 3. 注册 A/B（CloudMail 推荐 · CapSolver）

```bash
# 基线稳产
python scripts/prod_cloudmail_batch.py -n 4 -j 1 --ascii-log

# 日常甜点
python scripts/prod_cloudmail_batch.py -n 4 -j 2 --ascii-log

# 吞吐甜点（修 getBalance 缓存后）
python scripts/prod_cloudmail_batch.py -n 8 -j 4 --ascii-log

# 冲量（e495d3e 后 j8 可用；看 ok% 而非只看 wall）
python scripts/prod_cloudmail_batch.py -n 50 -j 8 --ascii-log

python main.py --bench-show
```

| j | 角色 | 备注 |
|---|------|------|
| 2 | 日常 | 最稳 |
| **4** | **吞吐甜点** | 日常优先 |
| 6–8 | 冲量 | 修后 j8n50/j8n80 **100%**（`e495d3e`） |
| 10–12 | 压测 | j10n50 / j12n40 **100%**；先 check-chain；wall 看长尾 |

记录：`ok%`、**成功号 p50**、`wall`、`primary_factor`、fail 分桶、proxy_retry 救回。

**停止：** j=2 ok% &lt; 75% → 停止加 j，转投递分诊。  
**停止：** 大量 `captcha_balance` + `ERROR_RATE_LIMIT` → 查 balance 缓存/单飞（`e495d3e`），勿盲加 j。  
**停止：** preflight FAIL → 等 15–30s 再 chain；**勿**立刻 `--skip-proxy-preflight`。

成功号 p50 健康窗 **~8.0～8.8s**（sso~3s，turnstile Cap~0.9s）。  
详：`output/efficiency/20260721_stress_r2_report.md` · `20260720_postfix_balance_cache.md`。
## 4. 读数

| 指标 | 含义 |
|------|------|
| wall | 批墙钟（吞吐） |
| per_ok | wall/ok（单成功成本） |
| primary_factor | 主瓶颈（成功号常 **sso**；Cap 路径 turnstile 已非墙） |
| short_body_rate | create 软拒长尾 |
| fail_buckets | 失败原因直方图 |

成功路径优化优先：**turnstile 预取（与 wait_code 重叠）/ soft-skip 零余额 / short-body**，不是加长 mail timeout。  
失败路径：**mail_timeout** → 双读分诊 DELIVERY，勿只换收码后端。

## 5. 后半段（mint / upload）

```bash
export MINT_PROXY=http://127.0.0.1:10808
export HTTPS_PROXY=http://127.0.0.1:10808

python main.py --mint all --mint-missing --no-probe -j 1 --limit 20 --ascii-log
python main.py --mint all --mint-missing --no-probe -j 4 --limit 20 --ascii-log

python main.py --export cpa
python main.py --cpa-upload all --cpa-missing -j 10 --limit 20
python main.py --cpa-upload all --cpa-missing -j 20 --limit 20
```

mint 并行 **勿** 默认 chat probe（models / `--no-probe`）。

**本机 2026-07-20 Phase D（limit=20）+ R2 mint 重试：**

| 阶段 | j | wall | ok% | 推荐 |
|------|---|------|-----|------|
| mint-missing | 1 | ~80s | 100 | 限流/排障 |
| mint-missing | 4 | ~22s | 100（1×429 自愈） | **日常甜点** |
| mint j4 + verify RL 重试 | 4 | ~22s / n=16 | **100**（2× RL 重试成功） | 限流窗仍可用 j4 |
| cpa-upload missing | 10 | ~2.8s | 100 | 稳 |
| cpa-upload missing | 20 | ~2.0s | 100 | **补洞甜点** |

详：`output/efficiency/20260720_phaseD_mint_upload.md` · 收口 `output/efficiency/20260720_efficiency_closeout.md`

## 6. 本机历史粗基线（prod_cloudmail，2026-07）

| j | 观察 |
|---|------|
| 1 | 稳，per_ok 贵 |
| 2 | ~98% ok，wall 中位 ~90s 级（甜点） |
| 8×100 | 高吞吐，需健康窗口 |

scrape cache 已把 scrape_p50≈0 → **再抠 scrape ROI 低**。

**2026-07 代码向优化（已合入）：**

| 项 | 作用 |
|----|------|
| captcha soft-skip | 零余额/死钥 TTL 内跳过，不每号烧 createTask |
| preferred provider | 余额预检 primary 优先（Yes 空 → Cap 先） |
| captcha probe single-flight | j>1 时空钱包只 1 次 createTask，成功后全并行 |
| captcha_prefetch | create_code 后并行打码，重叠 wait_code |
| **getBalance 缓存/单飞 `e495d3e`** | TTL~45s + RATE_LIMIT 退避；点名 backend 不误 skip |
| **gRPC wire-type `1528e55`** | 脏包 soft + in-call/prod 重试，勿硬杀批次 |
| mint adaptive interval | device/code 429 → 节流步进；成功衰减 |
| mint verify rate_limited | device/verify `error=rate_limited` 记节流并重试≤3 |
| bench primary | 按 OK 号 stage_sum 最大，不再 mode(top_stage) |
| R3 mail/captcha poll · light jitter · mint Session | 见 R3 台账 |

## 7. 改码门槛

仅当连续 ≥2 个可比 cell 有数据，且明确 **唯一** 改动点。  
禁止：注册 hop1=10808、关 short 重试、伪造 captcha、preflight 拒跑后盲 skip。  
日常 j 不必压到 1；**j=4～8 已是默认甜点～冲量带**（代理健康时）。