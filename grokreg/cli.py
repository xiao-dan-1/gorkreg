"""Grok 纯协议 CLI。"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from typing import Optional

from . import __version__
from .config import load_config
from .mail_cloudmail import allocate_cloudmail_address
from . import logutil

def configure_logging(verbose: bool = False, *, ascii_log: bool = False) -> None:
    logutil.configure_logging(verbose, ascii_log=ascii_log)

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grokreg",
        description="Grok / x.ai 纯协议注册（代理链式 + 发码/验码 + create + SSO）",
    )
    p.add_argument("--config", default=None, help="配置文件，默认 config.yaml")
    p.add_argument("--proxy", default=None, help="覆盖代理；empty/none/direct=直连")
    p.add_argument("--no-proxy", action="store_true", help="强制直连")
    p.add_argument("--region", default=None, help="动态代理地区 US/JP/NL/...")
    p.add_argument(
        "--check-proxy",
        action="store_true",
        help="检测出口 IP（动态代理会随机 sid + 链式隧道）",
    )
    p.add_argument(
        "--check-proxy-times",
        type=int,
        default=1,
        help="连续检测次数，验证换 sid 是否换 IP",
    )
    p.add_argument(
        "--check-chain",
        action="store_true",
        help="仅测 hop1 嵌套 CONNECT（7890 vs 10808）+ 全链出口，不注册",
    )
    p.add_argument(
        "--skip-proxy-preflight",
        action="store_true",
        help="批量/实验跳过注册前代理链自检（默认会做）",
    )
    p.add_argument(
        "--scrape",
        action="store_true",
        help="半链路：加载 sign-up 并动态刮 next-action",
    )
    p.add_argument(
        "--create-code",
        metavar="EMAIL",
        default=None,
        help="半链路：CreateEmailValidationCode",
    )
    p.add_argument(
        "--verify-code",
        nargs=2,
        metavar=("EMAIL", "CODE"),
        default=None,
        help="半链路：VerifyEmailValidationCode",
    )
    p.add_argument(
        "--register",
        metavar="MAIL_LINE_OR_EMAIL",
        default=None,
        help=(
            "完整注册。可传 email，或 email----password----client_id----refresh_token "
            "（后者会自动 Outlook 收码）"
        ),
    )
    p.add_argument(
        "--signup-email",
        metavar="ALIAS_OR_EMAIL",
        default=None,
        help=(
            "注册用邮箱（Outlook 同根别名）。收码仍用 --register 四段线主号 cid/rt；"
            "码进主箱。例: --register 'root----pw----cid----rt' --signup-email alias@outlook.com"
        ),
    )
    p.add_argument(
        "--password",
        default=None,
        help="注册密码；不传则随机生成",
    )
    p.add_argument(
        "--given-name",
        default="Jennifer",
        help="注册名 givenName",
    )
    p.add_argument(
        "--family-name",
        default="Mitchell",
        help="注册姓 familyName",
    )
    p.add_argument(
        "--code",
        default=None,
        help="已有验证码时跳过收码（如 5TT-GLT）；通常配合 --register 用",
    )
    p.add_argument(
        "--turnstile-token",
        default=None,
        help="手动注入 Turnstile token（跳过打码）",
    )
    p.add_argument(
        "--yescaptcha-key",
        default=None,
        help="YesCaptcha clientKey；也可用环境变量 YESCAPTCHA_API_KEY",
    )
    p.add_argument(
        "--twocaptcha-key",
        default=None,
        help="2Captcha API key；也可用环境变量 TWOCAPTCHA_API_KEY（推荐，勿写进 git）",
    )
    p.add_argument(
        "--capsolver-key",
        default=None,
        help="CapSolver clientKey；也可用环境变量 CAPSOLVER_API_KEY",
    )
    p.add_argument(
        "--captcha-backend",
        default=None,
        choices=("auto", "yescaptcha", "yes", "yc", "capsolver", "cap", "cs", "twocaptcha", "2captcha", "tc"),
        help=(
            "打码插件：auto（Yes→CapSolver→2C→local→browser）"
            " | yescaptcha | capsolver | twocaptcha。"
            "优先级：本 flag > env CAPTCHA_BACKEND > config captcha.backend > auto"
        ),
    )
    p.add_argument(
        "--skip-captcha-balance-check",
        action="store_true",
        help="跳过注册前 Yes/CapSolver/2C getBalance 预检（默认会查余额，0 则 fail-fast）",
    )
    p.add_argument(
        "--browser-turnstile",
        action="store_true",
        help="无 key：用本机 Chrome/Edge + Playwright 渲染 Turnstile（对照开源 local browser solver）",
    )
    p.add_argument(
        "--browser-channel",
        default="chrome",
        help="Playwright channel：chrome / msedge（默认 chrome）",
    )
    p.add_argument(
        "--browser-headless",
        action="store_true",
        help="浏览器无头模式（默认有头，便于点 checkbox）",
    )
    p.add_argument(
        "--local-solver-url",
        default=None,
        help="本地 Turnstile 服务根 URL（any-auto-register LocalSolver 风格 /turnstile+/result）",
    )
    p.add_argument(
        "--manual-turnstile",
        action="store_true",
        help="交互粘贴 Turnstile token（对照 ManualCaptcha）",
    )
    p.add_argument(
        "--castle-token",
        default="",
        help="Castle token（可选；参考实现常留空）",
    )
    p.add_argument(
        "--skip-create",
        action="store_true",
        help="只做到验码，不 create_account",
    )
    p.add_argument(
        "--output",
        default=None,
        help="成功账号 JSON 输出路径，默认 output/account_<ts>.json",
    )
    p.add_argument(
        "--batch",
        metavar="MAILS_FILE",
        default=None,
        help="串行批量注册：文件每行 email----pass----client_id----refresh_token（# 注释）",
    )
    p.add_argument(
        "--batch-delay",
        type=float,
        default=3.0,
        help="批量时每号间隔秒数（默认 3）",
    )
    p.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="并发线程数（默认 1=串行）；batch/mint/refresh/probe/upload 可用",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="批量时跳过 output/ 中已有 sso 的邮箱",
    )
    p.add_argument(
        "--ignore-mail-marks",
        action="store_true",
        help="批量时忽略 data/mail_marks.json 废号标记（默认跳过 dead/hold）",
    )
    p.add_argument(
        "--bench-backfill",
        action="store_true",
        help="从 output/*batch*.log 回填耗时台账（bench_runs.jsonl / bench_summary.md）",
    )
    p.add_argument(
        "--bench-show",
        action="store_true",
        help="打印最近注册耗时台账摘要",
    )
    p.add_argument(
        "--exp-round",
        action="store_true",
        help="跑一轮实验：从 mails.txt 取 N 个未注册号，-j 并发注册并写入 output/experiments/",
    )
    p.add_argument(
        "--exp-name",
        default="j10x10",
        help="实验名/目录（默认 j10x10 → output/experiments/j10x10）",
    )
    p.add_argument(
        "--exp-size",
        type=int,
        default=10,
        help="每轮账号数（默认 10）",
    )
    p.add_argument(
        "--exp-summary",
        action="store_true",
        help="汇总实验 rounds.jsonl（成功率/墙钟/short/失败分桶）",
    )
    p.add_argument(
        "--exp-status",
        action="store_true",
        help="查看实验进度：已跑轮次 + 剩余 free 邮件",
    )
    p.add_argument(
        "--mail-mark",
        metavar="EMAIL",
        default=None,
        help="标记邮箱为废号并跳过后续 batch（配合 --mail-mark-reason）",
    )
    p.add_argument(
        "--mail-mark-reason",
        default="",
        help="--mail-mark 原因（如 AADSTS70000 service abuse）",
    )
    p.add_argument(
        "--mail-mark-code",
        default="",
        help="--mail-mark 错误码（如 mail_auth / sso_failed）",
    )
    p.add_argument(
        "--mail-unmark",
        metavar="EMAIL",
        default=None,
        help="移除 mail_marks 中的邮箱标记",
    )
    p.add_argument(
        "--mail-marks",
        action="store_true",
        help="列出 data/mail_marks.json 废号标记",
    )
    p.add_argument(
        "--retry-failed",
        type=int,
        default=1,
        metavar="N",
        help="批量结束后对 retryable 失败串行补跑 N 轮（默认 1；0=关闭）",
    )
    p.add_argument(
        "--summary",
        action="store_true",
        help="汇总凭证账本 auth.json（唯一库存源）：fresh/needs/expired + domain；写 summary.csv",
    )
    p.add_argument(
        "--summary-full",
        action="store_true",
        help="配合 --summary：终端打印每号一行（默认只打摘要，避免刷屏）",
    )
    p.add_argument(
        "--summary-limit",
        type=int,
        default=30,
        metavar="N",
        help="--summary-full 时最多打印 N 行明细（默认 30；0=不限制，仍可能很长）",
    )
    p.add_argument(
        "--summary-domain",
        default=None,
        metavar="DOMAIN",
        help="配合 --summary：只统计邮箱域名包含 DOMAIN 的号（例 outlook.com）",
    )
    p.add_argument(
        "--check-sso",
        metavar="JSON_OR_EMAIL",
        default=None,
        help="校验结果 JSON 或邮箱对应最新账号的 SSO（JWT exp/session_id，不打印 token）",
    )
    p.add_argument(
        "--mail-backend",
        choices=["graph", "imap", "cloudmail"],
        default="graph",
        help="收码后端：graph=Outlook OAuth；imap=Outlook IMAP API；cloudmail=自建 CloudMail catch-all",
    )
    p.add_argument(
        "--mail-api-url",
        default="https://outlook.xdauv.xyz",
        help="IMAP 后端 API 地址（默认 https://outlook.xdauv.xyz）",
    )
    p.add_argument(
        "--register-cloudmail",
        action="store_true",
        help="分配 CloudMail catch-all 地址并完整注册（需 CLOUDMAIL_* / config.cloudmail）",
    )
    p.add_argument(
        "--cloudmail-alloc",
        action="store_true",
        help="仅打印一个 CloudMail 地址（不注册）",
    )
    p.add_argument(
        "--no-scrape-cache",
        action="store_true",
        help="禁用注册页元数据缓存（每号都重新 scrape；默认开启进程内短 TTL 缓存）",
    )
    p.add_argument(
        "--scrape-cache-ttl",
        type=float,
        default=600.0,
        metavar="SEC",
        help="scrape 公开页参数缓存秒数（默认 600；仅 next-action/sitekey，不含 cookie）",
    )
    p.add_argument(
        "--mail-sources",
        default=None,
        metavar="PATHS",
        help="号池文件，逗号/分号分隔（覆盖 config mail.sources / GROK_MAIL_SOURCES）",
    )
    p.add_argument(
        "--mail-pool-status",
        action="store_true",
        help="显示号池多源统计：sources / pool / free（不含密钥）",
    )
    p.add_argument(
        "--fixed-proxy",
        action="store_true",
        help="强制用 proxy.default（不走动态辣椒），便于协议调试",
    )
    p.add_argument(
        "--mint",
        metavar="EMAIL_OR_ALL",
        default=None,
        help="纯协议 mint：SSO→RT/AT→auth.json（默认不写 cpa_export；EMAIL 或 all）",
    )
    p.add_argument(
        "--mint-missing",
        action="store_true",
        help=(
            "配合 --mint：只 mint auth.json 尚无该邮箱的号"
            "（sso_roster 有 SSO；已在池中的跳过）"
        ),
    )
    p.add_argument(
        "--mint-write-cpa",
        action="store_true",
        help="配合 --mint：mint 时额外写 cpa_files pack（默认关闭；日常用 --export cpa_files）",
    )
    p.add_argument(
        "--mint-proxy",
        default=None,
        help="Mint 代理（默认 7890）；为空则用环境变量 HTTPS_PROXY",
    )
    p.add_argument(
        "--mint-out-dir",
        default=None,
        help="仅 --mint-write-cpa / --export cpa_files 时用的目录（默认 cpa_export/）",
    )
    p.add_argument(
        "--mint-probe-mode",
        choices=("models", "chat", "none"),
        default="models",
        help="mint/refresh 写盘后探活: models=默认快检 /models；chat=轻量 chat；none=不探",
    )
    p.add_argument(
        "--no-probe",
        action="store_true",
        help="mint/refresh 后不探活（等价 --mint-probe-mode none）",
    )
    p.add_argument(
        "--refresh",
        metavar="ALL_OR_EMAIL",
        default=None,
        help="刷新 auth.json 账本 access_token（ledger-first，不依赖 cpa_export）；all 或 EMAIL",
    )
    p.add_argument(
        "--remint-on-revoke",
        action="store_true",
        help=(
            "配合 --refresh：RT 被 revoke 时，"
            "用 sso_roster 的 SSO remint 写回 auth.json（默认不写 cpa_export；需有 SSO）"
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "调试用：--refresh/--mint/--auth-status/--probe-quota/--cpa-upload/--sub2api-upload 最多 N 个"
            "（过滤之后截断；mint-missing 时优先最新 SSO 号，不是最旧；0 或不设=不限制）"
        ),
    )
    p.add_argument(
        "--probe-quota",
        metavar="ALL_OR_EMAIL",
        default=None,
        help=(
            "探测账本 token 健康/额度（源=auth.json，不依赖 cpa_export）；all 或邮箱。"
            " 模式见 --probe-mode：models 探活；billing 账单；chat 限流头；quota=账单+限流"
        ),
    )
    p.add_argument(
        "--probe-mode",
        choices=("models", "chat", "both", "billing", "quota"),
        default="chat",
        help=(
            "probe 模式: models=快检 /models；billing=GET /billing 账单；"
            "chat=轻量 chat 读 x-ratelimit；quota=billing+chat；both=models 后 chat"
        ),
    )
    p.add_argument(
        "--probe-interval",
        type=float,
        default=None,
        help="批量 probe 间隔秒（默认 all=1.5，单号=0）",
    )
    p.add_argument(
        "--probe-retries",
        type=int,
        default=2,
        help="单号超时/失败重试次数（默认 2，共 3 次尝试）",
    )
    p.add_argument(
        "--probe-timeout",
        type=float,
        default=45.0,
        help="单次 HTTP 超时秒（默认 45）",
    )
    p.add_argument(
        "--probe-verbose",
        action="store_true",
        help="probe 打印每账号表体行（默认仅进度 + 失败明细，避免全量刷屏）",
    )
    p.add_argument(
        "--auth-file",
        default=None,
        help="多账号凭据池 auth.json 路径（默认 ./auth.json）",
    )
    p.add_argument(
        "--auth-import",
        metavar="XAI_DIR_OR_FILE",
        default=None,
        help="从 cpa_export 目录或单个 xAI pack 文件导入到 auth.json 池",
    )
    p.add_argument(
        "--auth-list",
        action="store_true",
        help="列出 auth.json 凭据池状态（email/exp/fresh）",
    )
    p.add_argument(
        "--auth-pick",
        metavar="EMAIL_OR_AUTO",
        default=None,
        help="从池中选号：EMAIL 指定，auto=选 freshest 活 token（只打印 email/exp，不打印 token）",
    )
    p.add_argument(
        "--export",
        nargs="?",
        const="sub2api",
        default=None,
        metavar="BACKEND",
        help=(
            "从 auth.json 批量出包（pack export 同级）。"
            "backend: cpa_files|cpa|cliproxy | sub2api | cockpit|cp；"
            "配合 --export-only / --export-out-dir / --export-dry-run"
        ),
    )
    p.add_argument(
        "--export-only",
        default=None,
        metavar="ALL_OR_EMAIL",
        help="配合 --export：过滤邮箱（默认 all）",
    )
    p.add_argument(
        "--export-out-dir",
        default=None,
        metavar="DIR",
        help=(
            "配合 --export：输出目录"
            "（cpa→cpa_export/ · sub2api→sub2api_export/ · cockpit→cockpit_export/）"
        ),
    )
    p.add_argument(
        "--export-dry-run",
        action="store_true",
        help="配合 --export：只统计路径，不写文件",
    )
    p.add_argument(
        "--sub2api-export",
        nargs="?",
        const="all",
        default=None,
        metavar="ALL_OR_EMAIL",
        help=(
            "别名：等价于 --export sub2api --export-only … "
            "（auth.json → UI 导入包 {exported_at,proxies,accounts}）"
        ),
    )
    p.add_argument(
        "--sub2api-out-dir",
        default=None,
        metavar="DIR",
        help="配合 --sub2api-export / --export sub2api：输出目录",
    )
    p.add_argument(
        "--sub2api-dry-run",
        action="store_true",
        help="配合 --sub2api-export：dry-run（也可用 --export-dry-run）",
    )
    p.add_argument(
        "--sub2api-no-model-mapping",
        action="store_true",
        help="配合 sub2api export：credentials 不写 model_mapping",
    )
    p.add_argument(
        "--sub2api-upload",
        nargs="?",
        const="all",
        default=None,
        metavar="ALL_OR_EMAIL",
        help=(
            "远程 sub2api admin 导入（POST /api/v1/admin/accounts/data）。"
            "默认读 sub2api_export/grok-*.json；--sub2api-from-auth 则从 auth.json 组包。"
            "凭据：SUB2API_BASE_URL / SUB2API_ADMIN_EMAIL / SUB2API_ADMIN_PASSWORD"
        ),
    )
    p.add_argument(
        "--sub2api-from-auth",
        action="store_true",
        help="配合 --sub2api-upload：从 auth.json 现场组包（可不先 --export）",
    )
    p.add_argument(
        "--sub2api-upload-dry-run",
        action="store_true",
        help="配合 --sub2api-upload：只统计账号数，不 POST",
    )
    p.add_argument(
        "--sub2api-on-exists",
        default="create",
        choices=("create", "skip", "overwrite", "update", "upsert"),
        help=(
            "远端已存在同 platform+email 时："
            "create=仍新建(默认,可重复)；skip=跳过；"
            "overwrite=删掉远端同平台同邮箱全部旧号，再导入本次这一条（绝不跨平台）"
        ),
    )
    p.add_argument(
        "--sub2api-no-skip-default-group",
        action="store_true",
        help="配合 --sub2api-upload：skip_default_group_bind=false（默认 true，对齐 UI）",
    )
    p.add_argument(
        "--env-check",
        action="store_true",
        help="检查 .env / 关键环境变量是否已加载（不打印密钥全文）",
    )
    p.add_argument(
        "--cpa-upload",
        nargs="?",
        const="all",
        default=None,
        metavar="ALL_OR_EMAIL",
        help="上传本地 cpa_export 到 CLIProxy 管理 API（all 或指定邮箱）",
    )
    p.add_argument(
        "--cpa-list",
        action="store_true",
        help="列出服务器已加载的 CLIProxy 凭证（GET /v0/management/auth-files）",
    )
    p.add_argument(
        "--cpa-dry-run",
        action="store_true",
        help="配合 --cpa-upload：只列出将上传的文件，不真正 POST",
    )
    p.add_argument(
        "--cpa-missing",
        action="store_true",
        help="配合 --cpa-upload：只上传远端 list 没有的本地 xai-*.json",
    )
    p.add_argument(
        "--auth-status",
        nargs="?",
        const="all",
        default=None,
        metavar="ALL_OR_EMAIL",
        help="账本 auth.json 状态（源=auth.json，非 cpa_export）：all 或邮箱；只读",
    )

    p.add_argument(
        "--recover-sso-roster",
        action="store_true",
        help="从 output/accounts/（含 legacy output/ 根）回填 sso_roster",
    )
    p.add_argument(
        "--recover-output-dir",
        default="output",
        help="--recover-sso-roster 的 output 目录（默认 output）",
    )
    p.add_argument(
        "--recover-dry-run",
        action="store_true",
        help="配合 --recover-sso-roster：只统计不写 sso_roster",
    )
    p.add_argument(
        "--sso-audit",
        action="store_true",
        help="巡检 sso_roster / output / auth.json 三落点差集（不写盘）",
    )
    p.add_argument(
        "--migrate-sso-roster",
        action="store_true",
        help="重写 sso_roster 为 email----password----sso（从 evidence 补密码；可 --recover-dry-run）",
    )
    p.add_argument(
        "--migrate-account-evidence",
        action="store_true",
        help="把 output/account_*.json 迁到 output/accounts/（可 --recover-dry-run）",
    )

    p.add_argument(
        "--skew-min",
        type=float,
        default=5.0,
        metavar="MIN",
        help="配合 --auth-status / --refresh：剩余不足 N 分钟视为 needs_refresh（默认 5）",
    )
    p.add_argument(
        "--needs-refresh-only",
        action="store_true",
        help=(
            "配合 --auth-status：只打印 needs_refresh/expired；"
            "配合 --refresh：只刷 needs_refresh/expired 且有 RT 的号"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    p.add_argument(
        "--ascii-log",
        action="store_true",
        help="日志/进度用 ASCII 图标（OK/FAIL），避免终端 Unicode 问题",
    )
    p.add_argument("--version", action="version", version=f"grokreg {__version__}")
    return p

def _resolve_proxy_arg(args: argparse.Namespace) -> Optional[str]:
    if args.no_proxy:
        return ""
    if args.proxy is None:
        return None
    if args.proxy.strip().lower() in {"empty", "none", "direct", ""}:
        return ""
    return args.proxy.strip()

def _apply_region(cfg: dict, region: Optional[str]) -> None:
    if not region:
        return
    dyn = cfg.setdefault("proxy", {}).setdefault("dynamic", {})
    dyn["region"] = region.strip().upper()
    if not dyn.get("enabled") and (dyn.get("template") or dyn.get("user")):
        dyn["enabled"] = True

def _force_fixed_proxy(cfg: dict) -> None:
    dyn = cfg.setdefault("proxy", {}).setdefault("dynamic", {})
    dyn["enabled"] = False

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(
        args.verbose,
        ascii_log=bool(getattr(args, "ascii_log", False)),
    )
    logutil.new_run_id()

    cfg = load_config(args.config)
    _apply_region(cfg, args.region)
    if args.fixed_proxy:
        _force_fixed_proxy(cfg)

    proxy_override = _resolve_proxy_arg(args)

    if args.check_proxy:
        return _cmd_check_proxy(cfg, proxy_override, times=max(1, args.check_proxy_times))
    if getattr(args, "check_chain", False):
        return _cmd_check_chain(cfg, proxy_override)

    if args.summary:
        return _cmd_summary(cfg, args)

    if getattr(args, "bench_backfill", False):
        return _cmd_bench_backfill()
    if getattr(args, "bench_show", False):
        return _cmd_bench_show()
    if getattr(args, "mail_pool_status", False):
        return _cmd_mail_pool_status(cfg, args)
    if getattr(args, "exp_status", False):
        return _cmd_exp_status(cfg, args)
    if getattr(args, "exp_summary", False):
        return _cmd_exp_summary(args)
    if getattr(args, "exp_round", False):
        return _cmd_exp_round(cfg, proxy_override, args)
    if getattr(args, "mail_marks", False):
        return _cmd_mail_marks_list()
    if getattr(args, "mail_mark", None):
        return _cmd_mail_mark(args)
    if getattr(args, "mail_unmark", None):
        return _cmd_mail_unmark(args)

    if args.check_sso:
        return _cmd_check_sso(cfg, args.check_sso)

    if getattr(args, "recover_sso_roster", False):
        return _cmd_recover_sso_roster(cfg, args)
    if getattr(args, "migrate_sso_roster", False):
        return _cmd_migrate_sso_roster(cfg, args)
    if getattr(args, "migrate_account_evidence", False):
        return _cmd_migrate_account_evidence(cfg, args)

    if getattr(args, "sso_audit", False):
        return _cmd_sso_audit(cfg, args)

    if args.env_check:
        return _cmd_env_check(cfg)

    if args.cpa_list:
        return _cmd_cpa_list(cfg)

    if getattr(args, "auth_status", None) is not None:
        return _cmd_auth_status(cfg, args)

    if args.cpa_upload is not None:
        return _cmd_cpa_upload(cfg, args)

    if getattr(args, "sub2api_upload", None) is not None:
        return _cmd_sub2api_upload(cfg, args)

    if args.auth_import:
        return _cmd_auth_import(cfg, args)

    if args.auth_list:
        return _cmd_auth_list(cfg, args)

    if args.auth_pick is not None:
        return _cmd_auth_pick(cfg, args)

    if getattr(args, "export", None) is not None or getattr(args, "sub2api_export", None) is not None:
        return _cmd_export(cfg, args)

    if getattr(args, "mint", None):
        return _cmd_mint(cfg, args)

    if getattr(args, "refresh", None):
        return _cmd_refresh(cfg, args)

    if args.probe_quota:
        return _cmd_probe_quota(cfg, args)

    if args.batch:
        return _cmd_batch(cfg, proxy_override, args)

    if getattr(args, "cloudmail_alloc", False) and not getattr(args, "register_cloudmail", False):
        try:
            print(allocate_cloudmail_address(cfg))
            return 0
        except Exception as exc:
            logging.error("cloudmail alloc: %s", exc)
            return 1

    if getattr(args, "register_cloudmail", False):
        return _cmd_register_cloudmail(cfg, proxy_override, args)

    if args.register:
        return _cmd_register(cfg, proxy_override, args)

    if args.scrape or args.create_code or args.verify_code:
        return _cmd_half_chain(cfg, proxy_override, args)

    parser.print_help()
    print(
        "\n常用:\n"
        "  python main.py --check-proxy --check-proxy-times 3\n"
        "  python main.py --check-chain\n"
        "  python main.py --cpa-upload all --cpa-missing -j 20\n"
        "  python main.py --fixed-proxy --scrape\n"
        "  python main.py --register 'email----pass----cid----rt' -v\n"
        "  python main.py --register-cloudmail -v\n"
        "  python main.py --batch mails.txt --region US -v\n"
        "  python main.py --summary\n"
        "  python main.py --check-sso JenniferMitchell9500@outlook.com\n"
        "  python main.py --mint all\n"
        "  python main.py --mint jennifer@outlook.com\n"
    )
    return 0

# ---- credential lifecycle (ops.credential_cmds) ----
from .ops.mint_cmds import _cmd_mint  # noqa: E402
from .ops.credential_cmds import (  # noqa: E402
    _append_sso_roster,
    _auth_path,
    _cmd_auth_status,
    _cmd_probe_quota,
    _cmd_refresh,
    _cpa_path_for_email,
    _existing_cpa_emails,
    _existing_pool_emails,
    _read_sso_roster,
)

# ---- export / upload / auth-pool (ops.export_cmds) ----
from .ops.export_cmds import (  # noqa: E402
    _cmd_auth_import,
    _cmd_auth_list,
    _cmd_auth_pick,
    _cmd_cpa_list,
    _cmd_cpa_upload,
    _cmd_export,
    _cmd_sub2api_export,
    _cmd_sub2api_upload,
    _sync_auth_from_cpa_path,
)

# ---- env / proxy (ops.env_cmds) ----
from .ops.env_cmds import (  # noqa: E402
    _cmd_check_chain,
    _cmd_check_proxy,
    _cmd_env_check,
    _cmd_half_chain,
    _run_proxy_preflight,
    run_proxy_preflight,
)

# ---- register / batch / summary / mail / exp (ops.register_cmds) ----
from .ops.register_cmds import (  # noqa: E402
    _cmd_batch,
    _cmd_bench_backfill,
    _cmd_bench_show,
    _cmd_check_sso,
    _cmd_exp_round,
    _cmd_exp_status,
    _cmd_exp_summary,
    _cmd_mail_mark,
    _cmd_mail_marks_list,
    _cmd_mail_pool_status,
    _cmd_mail_unmark,
    _cmd_migrate_sso_roster,
    _cmd_migrate_account_evidence,
    _cmd_recover_sso_roster,
    _cmd_sso_audit,
    _cmd_register,
    _cmd_register_cloudmail,
    _cmd_summary,
    _save_result,
    _summary_error_bucket,
)

if __name__ == "__main__":
    sys.exit(main())

