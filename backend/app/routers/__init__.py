"""业务路由汇总：失败重试派工专用路由 + 通用资源路由。

retry 的 /api/retry/* 路径更具体，必须先于 resource 的 /api/{module} 注册，
否则会被通用动态路径抢先匹配。
"""
from __future__ import annotations

from app.routers import resource, retry

ROUTERS = [retry, resource]
