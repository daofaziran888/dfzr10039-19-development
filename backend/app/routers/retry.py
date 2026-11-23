"""失败尝试重试派工接口：查询、清单、派工、进程和成功回执。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.failure_ingest import ingest_local_page, ingest_log_file
from app.services.retry_dispatch import retry_dispatch_service as service

router = APIRouter(prefix="/api/retry", tags=["失败重试派工"])


class FailedAttemptPayload(BaseModel):
    作业编号: str
    来源地址: str
    输入内容: str
    异常类型: str = "缺标题"
    解析规则: str = ""
    错误详情: str = ""


class IngestPagePayload(BaseModel):
    作业编号: str
    来源地址: str
    page_path: str
    解析规则: str = ""


class IngestLogPayload(BaseModel):
    log_path: str


class RetryListPayload(BaseModel):
    作业编号: str
    attempt_ids: list[int] = Field(default_factory=list)


class ClaimPayload(BaseModel):
    领取人: str = "采集任务"


class SuccessReceiptPayload(BaseModel):
    作业编号: str
    来源地址: str
    输入内容: str | None = None
    输入指纹: str | None = None
    回执来源: str = "补录"


# ---------------------------------------------------------------------------
# 失败尝试：登记、本地网页/日志录入、游标查询、详情
# 注意：固定路径必须注册在 /failed-attempts/{attempt_id} 之前
# ---------------------------------------------------------------------------

@router.post("/failed-attempts")
def register_failed_attempt(payload: FailedAttemptPayload) -> dict[str, Any]:
    row = service.register_failed_attempt(**payload.model_dump())
    return {"ok": True, "message": f"失败尝试 {row['id']} 已登记", "entry": row}


@router.post("/failed-attempts/ingest-page")
def ingest_page(payload: IngestPagePayload) -> dict[str, Any]:
    try:
        row = ingest_local_page(
            作业编号=payload.作业编号, 来源地址=payload.来源地址,
            page_path=payload.page_path, 解析规则=payload.解析规则,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"本地网页不存在：{payload.page_path}")
    if row is None:
        return {"ok": True, "message": "页面标题完整，未产生缺标题失败尝试", "entry": None}
    return {"ok": True, "message": f"本地网页已放入，失败尝试 {row['id']} 已登记", "entry": row}


@router.post("/failed-attempts/ingest-log")
def ingest_log(payload: IngestLogPayload) -> dict[str, Any]:
    try:
        rows = ingest_log_file(payload.log_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"日志文件不存在：{payload.log_path}")
    return {"ok": True, "message": f"日志已放入，登记 {len(rows)} 条失败尝试",
            "items": rows}


@router.get("/failed-attempts")
def query_failed_attempts(
    作业编号: str | None = Query(default=None),
    异常类型: str | None = Query(default=None),
    cursor: int | None = Query(default=None),
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """按作业和异常类型筛选失败尝试；带 cursor 接着上次读到的继续读。"""
    items, next_cursor = service.query_failed_attempts(
        作业编号=作业编号, 异常类型=异常类型, cursor=cursor, size=size)
    return {"items": items, "next_cursor": next_cursor, "size": size}


@router.get("/failed-attempts/{attempt_id}")
def get_failed_attempt(attempt_id: int) -> dict[str, Any]:
    detail = service.get_failed_attempt(attempt_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"失败尝试 {attempt_id} 不存在")
    return detail


# ---------------------------------------------------------------------------
# 重试候选清单：组装、查询、领取、派工、重送
# ---------------------------------------------------------------------------

@router.post("/lists")
def assemble_retry_list(payload: RetryListPayload) -> dict[str, Any]:
    list_row, message = service.assemble_retry_list(
        作业编号=payload.作业编号, attempt_ids=payload.attempt_ids)
    return {"ok": list_row is not None, "message": message, "entry": list_row}


@router.get("/lists")
def list_retry_lists(作业编号: str | None = Query(default=None)) -> dict[str, Any]:
    return {"items": service.list_retry_lists(作业编号=作业编号)}


@router.post("/lists/{list_id}/claim")
def claim_list(list_id: int, payload: ClaimPayload) -> dict[str, Any]:
    list_row, message = service.claim_list(list_id, 领取人=payload.领取人)
    return {"ok": list_row is not None, "message": message, "entry": list_row}


@router.post("/lists/{list_id}/dispatch")
def dispatch_list(list_id: int) -> dict[str, Any]:
    results, message = service.dispatch_list(list_id, 来源="旧派工")
    if results is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "items": results}


@router.post("/lists/{list_id}/resend")
def resend_list(list_id: int) -> dict[str, Any]:
    results, message = service.resend_list(list_id)
    if results is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "items": results}


@router.get("/pending/{job_id}")
def job_pending_list(job_id: str) -> dict[str, Any]:
    """采集任务的待执行列表：由重试候选清单驱动。"""
    return {"items": service.job_pending_list(job_id)}


# ---------------------------------------------------------------------------
# 采集进程：状态、暂停、恢复
# ---------------------------------------------------------------------------

@router.get("/process/{job_id}")
def process_state(job_id: str) -> dict[str, Any]:
    return service.process_state(job_id)


@router.post("/process/{job_id}/pause")
def pause_process(job_id: str) -> dict[str, Any]:
    process = service.pause_process(job_id)
    return {"ok": True, "message": f"作业 {job_id} 采集进程已暂停", "entry": process}


@router.post("/process/{job_id}/resume")
def resume_process(job_id: str) -> dict[str, Any]:
    process, results = service.resume_process(job_id)
    return {"ok": True, "message": f"作业 {job_id} 采集进程已恢复",
            "entry": process, "items": results}


# ---------------------------------------------------------------------------
# 成功回执：补录 / 实采登记、查询
# ---------------------------------------------------------------------------

@router.post("/success-receipts")
def record_success(payload: SuccessReceiptPayload) -> dict[str, Any]:
    receipt, message = service.record_success(**payload.model_dump())
    return {"ok": bool(receipt), "message": message, "entry": receipt or None}


@router.get("/success-receipts")
def list_success_receipts(
    作业编号: str | None = Query(default=None),
    来源地址: str | None = Query(default=None),
) -> dict[str, Any]:
    return {"items": service.list_success_receipts(作业编号=作业编号, 来源地址=来源地址)}
