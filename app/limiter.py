"""全局速率限制器（PRD §7B.2）。"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# 默认全局 120/min，敏感接口单独加 @limiter.limit("10/minute")
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
