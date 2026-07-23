# Outlook 同根别名注册（用户校正 · 待测上限）

> 状态：经验已记录；**每根号可挂多少别名仍未知**，用台账累计。  
> 日期：2026-07-14

## 结论（用户确认）

| 点 | 说明 |
|----|------|
| **同根别名可注册** | Outlook **plus 寻址**：`主号local+任意tag@outlook.com`（例 `JustinColon4805+xd8f0@outlook.com`） |
| **不是** | 另拼一套 `localxai1@outlook.com` 假地址（未绑定，create 可 200 但收不到码） |
| **收码不换凭据** | 验证码进**主邮箱收件箱**；Graph/IMAP 仍用主号 `client_id + refresh_token` 读 |
| **注册邮箱字段** | create / SSO 用 **`local+tag@...`**；账本按别名记 1 个号 |
| **上限** | **未知** — 不要写死 N；每根号成功别名数写入台账 |
| **真机** | 2026-07-15：`JustinColon4805+xd8f0` create+收码~8s+SSO 全过（第 1 个 plus） |
| **≠ CloudMail** | CloudMail = 自建 catch-all 域；本条 = Microsoft 账号别名 |
| **≠ 号池 free** | `mails.txt` free=0 只说明主号线用尽；别名是另一扩容轴 |

## 和现有流水线的关系

```text
主号线（mails.txt）:
  root@outlook.com----pw----cid----rt
  → --register / --batch  注册邮箱 = root

别名路径（概念）:
  注册邮箱 = alias@outlook.com（或其它已绑到该 Microsoft 账户的别名）
  收码凭据 = 仍用 root 的 cid/rt（Graph me/messages 能看到别名投递）
  账本 SSO 行 = alias----password----sso   （按别名记，勿和 root 混成同一 Grok 号）
```

CLI 已支持 **`--signup-email`**（注册邮箱=别名；收码=主号四段线）。**没有**自动批量派生别名。

```bash
python main.py --check-chain
python main.py --register "root@outlook.com----pw----cid----rt" ^
  --signup-email "rootlocal+tag@outlook.com" --region US -v
# 结果 JSON：email=alias，mail_root=root，is_alias=true
# 真机一条后写入 output/outlook_alias_ledger.md
```

**仍可选（未做）**：

- 批量 `mails_alias.txt` / 自动从主号派生 N 个别名  
- 上限测清前不必急着做；先台账。

## 风险与分诊

| 现象 | 优先怀疑 |
|------|----------|
| create 200 + 双读空 | 投递/别名未生效 / 主箱过滤，≠「别名协议不通」 |
| create 拒 / already | 该别名已被占用或根号风控 |
| mail_auth | **主号 RT 废**，别名再多也收不到 |
| 同根第 N 个别名开始挂 | **可能撞上限或频控** → 记 N，停烧该 root |

## 台账（追加，勿猜）

路径建议：`output/outlook_alias_ledger.md`（gitignore 的 output 下；可复制本表）。

| 日期 | root 主号 | alias | 序号(该 root 第几个) | create | 收码 | SSO | 备注 |
|------|-----------|-------|----------------------|--------|------|-----|------|
| _(空)_ | | | | | | | 上限未知，测到再填 |

汇总行（人工维护）：

```text
root_email | aliases_ok | aliases_fail | first_fail_n | notes
```

## 和库存口径

- `sso_roster` / inventory：**按注册邮箱（别名）计 1 个通过号**  
- 主号是否本身也注册过：可同时存在 root 号 + 多个 alias 号  
- free 池：`mails.txt` 仍只描述**主号线**；别名成功**不**自动减少 free 主号

## 变更记录

| 日 | 内容 |
|----|------|
| 2026-07-14 | 用户指出同根别名可注册；上限未知；本文 + memory |
| 2026-07-15 | CLI `--signup-email` + result `is_alias`/`mail_root` |
| 2026-07-15 | 澄清 plus `local+tag@`；真机 JustinColon4805+xd8f0 全过 |
