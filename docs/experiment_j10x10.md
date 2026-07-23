# j10×10 注册实验设计

## 目标

用固定协议、固定并发，分轮采样，回答：

1. **成功率**（ok / n）
2. **稳定性**（轮间 success_rate 的 mean±stdev，是否随轮次漂移）
3. **耗时**（墙钟 wall、每成功 per_ok、OK 中位/ p90、阶段 mean）
4. **隐性成本**（short-body 率、失败分桶、失败烧时）

参考思路（负载/A-B 实验常见做法）：

- **单变量**：本系列只变「轮次时间」，固定 `j=10, n=10, graph, retry=0`
- **固定样本块**：每轮 10 号，避免一次 100 并发把失败原因搅在一起
- **分桶失败**：mail_timeout / create / captcha / sso … 不要只看总成功率
- **长尾**：看 p90 与 short 重试，不只看均值
- **轮间间隔可选**：若代理/打码限流，可在两轮间休息 30–60s

## 控制变量（默认）

| 项 | 值 |
|---|---|
| jobs | **10** |
| 每轮 n | **10** |
| mail_backend | graph |
| 收码代理 | direct（register 已解耦） |
| 注册代理 | 辣椒动态 |
| retry_failed | **0**（看原始 j10，不串行补跑掺水） |
| short-body 重试 | 仍开启（最多 3） |

## 指标

| 指标 | 含义 |
|---|---|
| success_rate | 本轮 ok/n |
| wall_sec | 墙钟（吞吐） |
| wall_per_ok | wall/ok |
| ok_elapsed mean/median/p90 | 成功号体感耗时 |
| stage_mean | scrape/wait/turnstile/create/sso… |
| short_body_rate | 经历 short 的账号占比 |
| fail_buckets | error_code 直方图 |
| factor_counts | 主瓶颈标签 |

## 你可能没单独盯、但本实验会记的

1. **short-body 率**（假成功长尾）  
2. **失败烧时** fail_elapsed（mail_timeout 常吃满 120s）  
3. **阶段份额** stage_share（到底是打码还是投递）  
4. **轮间漂移**（第 1 轮 10/10，第 5 轮 3/10 → 限流/池子疲劳）  
5. **并发叠加** j10 下 turnstile 排队、代理 SID 碰撞（看 factor + 日志 sid）  
6. **表观成功 vs SSO**（已有 sso 才算 ok）

## 命令

```bash
# 1) 把 100 个新号追加进 mails.txt（email----pass----client_id----refresh_token）

# 2) 看池子
python main.py --exp-status

# 3) 跑一轮（默认 j=10, n=10）
python main.py --exp-round

# 或显式
python main.py --exp-round -j 10 --exp-size 10 --retry-failed 0

# 4) 每轮后看汇总
python main.py --exp-summary

# 5) 重复 3→10 直到 free 不够
```

产物：

```text
output/experiments/j10x10/
  round_01_batch.txt
  rounds.jsonl
  summary.md
output/bench_runs.jsonl          # 每轮也进总台账
```

## 分析怎么读

| 现象 | 解读 |
|---|---|
| rate 高且 wall≈单号 turnstile | j10 吞吐健康 |
| rate 高但 wall 接近 单号×10 | 实际几乎串行（打码/代理瓶颈） |
| short% 高 | create 软拒；与收码无关 |
| fail 集中 mail_timeout | 投递/并发撞车，不是 Graph 读坏 |
| 后几轮 rate 塌 | 限流/号池/代理质量随时间变差 |



## 结果口径（j10x10 已跑完 — 固化）

跑完后读数用 **两套口径**，禁止混用：

| 口径 | 数字 | 何时用 |
|------|------|--------|
| **raw** | 本轮/`--exp-summary` 的 ok/n（本系列合计 **81/100**） | 评估 j10 并发、轮间漂移、原始 fail 分桶；`retry_failed=0` |
| **final** | 补救后名额（本系列 **93/100 OK**） | mint / CPA /「池子有多少可用」 |

补救不计入 raw：r10 代理塌后整批重跑、有限 retry；未成号 → mail_timeout **hold**(×4) / **dead**(Shawna×1)。

产物补充：

```text
output/experiments/j10x10/
  summary.md       # raw 表 + 口径说明
  status_100.md    # final 93 + HOLD/DEAD
  status_100.json
  rounds.jsonl
```

一句话：**问稳定性看 81%；问能上多少号看 93%。**

## 不建议本系列同时改的

- 同时开 retry_failed  
- 中途改 j / backend  
- 盲改协议核心  

若要对比 j=4 vs j=10：另开 `--exp-name j4x10` 再跑一组。
