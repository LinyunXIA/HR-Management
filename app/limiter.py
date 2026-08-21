"""全局速率限制器（PRD §7B.2）。

storage_uri 可通过 LIMITER_STORAGE_URI 覆盖，默认 memory://（单实例）；
多实例部署时设为 redis://host:port（如 redis://localhost:6379）。
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

_STORAGE_URI = os.environ.get("LIMITER_STORAGE_URI", "memory://")

# 默认全局 120/min，敏感接口单独加 @limiter.limit("10/minute")
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"], storage_uri=_STORAGE_URI)
