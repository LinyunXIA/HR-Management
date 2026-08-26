"""全局速率限制器（PRD §7B.4）。

限额可通过环境变量覆盖（issue #127，消除硬编码；.env.example 同名键）：
- RATE_LIMIT_DEFAULT  全局默认（默认 120/minute）
- RATE_LIMIT_LOGIN    登录类端点（默认 10/minute）
- RATE_LIMIT_PUBLIC   对外 /public/* 端点（默认 60/minute）

storage_uri 可通过 LIMITER_STORAGE_URI 覆盖，默认 memory://（单实例）；
多实例部署时设为 redis://host:port（如 redis://localhost:6379）。
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address


def _limit(key: str, default: str) -> str:
    return os.environ.get(key, default).strip() or default


RATE_LIMIT_DEFAULT = _limit("RATE_LIMIT_DEFAULT", "120/minute")
RATE_LIMIT_LOGIN = _limit("RATE_LIMIT_LOGIN", "10/minute")
RATE_LIMIT_PUBLIC = _limit("RATE_LIMIT_PUBLIC", "60/minute")

_STORAGE_URI = os.environ.get("LIMITER_STORAGE_URI", "memory://")

# 默认全局 RATE_LIMIT_DEFAULT，敏感接口单独加 @limiter.limit(RATE_LIMIT_LOGIN)
limiter = Limiter(key_func=get_remote_address,
                  default_limits=[RATE_LIMIT_DEFAULT],
                  storage_uri=_STORAGE_URI)
