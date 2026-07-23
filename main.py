#!/usr/bin/env python3
"""Grok 纯协议注册入口。"""
from grokreg.config import ensure_dotenv

ensure_dotenv()

from grokreg.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
