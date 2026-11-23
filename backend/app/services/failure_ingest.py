"""本地网页与日志的失败尝试录入。

采集侧产出的缺标题记录有两个来源：
- 本地网页：抓回本地的 HTML 页面，缺 <title> 节点即记一条「缺标题」失败尝试；
- 日志文件：解析运行日志里的失败行（JSON Lines），逐行登记失败尝试。

同一地址换了一份输入，页面内容不同 → 输入指纹不同 → 是新的失败尝试。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.retry_dispatch import retry_dispatch_service

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

DEFAULT_TITLE_RULE = "title://html/head/title"


def ingest_local_page(
    *,
    作业编号: str,
    来源地址: str,
    page_path: str | Path,
    解析规则: str = "",
) -> dict[str, Any] | None:
    """解析一个本地网页，缺标题则登记一条失败尝试，否则返回 None。"""
    content = Path(page_path).read_text(encoding="utf-8")
    match = _TITLE_RE.search(content)
    title = match.group(1).strip() if match else ""
    if title:
        return None
    return retry_dispatch_service.register_failed_attempt(
        作业编号=作业编号,
        来源地址=来源地址,
        输入内容=content,
        异常类型="缺标题",
        解析规则=解析规则 or DEFAULT_TITLE_RULE,
        错误详情="页面缺少<title>标题节点，标题字段抽取失败",
    )


def ingest_log_file(log_path: str | Path) -> list[dict[str, Any]]:
    """逐行解析失败日志（JSON Lines），登记失败尝试，返回登记记录。"""
    registered: list[dict[str, Any]] = []
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        registered.append(retry_dispatch_service.register_failed_attempt(
            作业编号=str(payload["作业编号"]),
            来源地址=str(payload["来源地址"]),
            输入内容=str(payload["输入内容"]),
            异常类型=str(payload.get("异常类型", "缺标题")),
            解析规则=str(payload.get("解析规则", "")),
            错误详情=str(payload.get("错误详情", "")),
        ))
    return registered
