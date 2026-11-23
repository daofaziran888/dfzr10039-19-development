"""失败尝试重试派工验收测试。

场景：夜班选中作业 J72 的 A1、B1 组装重试候选清单，任务已领取但派工停在
进程启动前；白班补录同一输入 A1 的成功回执；迟到的旧派工与重送并发到达，
已成功的 A1 不能再跑、B1 不能开跑两次；恢复进程后只有 B1 开跑一次；
再给 B1 记成功，提交同地址新输入 B2，重新筛选并领取 B2。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services.failure_ingest import ingest_local_page, ingest_log_file
from app.services.retry_dispatch import (
    ATTEMPT_BLOCKED,
    ATTEMPT_OPEN,
    ATTEMPT_STARTED,
    ATTEMPT_SUCCEEDED,
    LIST_DONE,
    REASON_PROCESS_DOWN,
    REASON_SUCCESS_RECEIPT,
    retry_dispatch_service as svc,
)
from app.store import store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "retry"
JOB = "J72"
OTHER_JOB = "J73"
ADDR = "local://news.local/article/72"

PAGE_A1 = (FIXTURES / "page_a1.html").read_text(encoding="utf-8")
PAGE_B1 = (FIXTURES / "page_b1.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def scenario_records() -> dict[str, int]:
    """用本地网页和日志放入 A1、B1 的缺标题记录及另一作业的同地址失败。"""
    a1 = ingest_local_page(作业编号=JOB, 来源地址=ADDR, page_path=FIXTURES / "page_a1.html")
    b1 = ingest_local_page(作业编号=JOB, 来源地址=ADDR, page_path=FIXTURES / "page_b1.html")
    logged = ingest_log_file(FIXTURES / "j73_same_address.log")
    assert a1 is not None and b1 is not None and len(logged) == 1
    return {"a1": int(a1["id"]), "b1": int(b1["id"]), "j73": int(logged[0]["id"])}


def _attempt(attempt_id: int) -> dict:
    detail = svc.get_failed_attempt(attempt_id)
    assert detail is not None
    return detail


def _starts_for(作业编号: str, 输入指纹: str) -> list[dict]:
    return [
        row for row in store.rows("dispatch_start")
        if row.get("作业编号") == 作业编号 and row.get("输入指纹") == 输入指纹
    ]


# ---------------------------------------------------------------------------
# 读取失败尝试：按作业和异常类型筛选，游标续读翻两页
# ---------------------------------------------------------------------------

def test_ingest_and_cursor_paging(scenario_records: dict[str, int]) -> None:
    """本地网页与日志放入失败尝试；按作业+异常类型筛选，翻两页读完。"""
    a1 = _attempt(scenario_records["a1"])
    b1 = _attempt(scenario_records["b1"])
    assert a1["异常类型"] == "缺标题" and b1["异常类型"] == "缺标题"
    # 同一地址、不同输入 → 不同输入指纹
    assert a1["来源地址"] == b1["来源地址"] == ADDR
    assert a1["输入指纹"] != b1["输入指纹"]

    # 第一页（size=1）：接着上次继续读靠 next_cursor
    page1, cursor1 = svc.query_failed_attempts(作业编号=JOB, 异常类型="缺标题", size=1)
    assert [row["id"] for row in page1] == [scenario_records["a1"]]
    assert cursor1 is not None

    # 第二页：传入上次游标，读到 B1，后面没有了
    page2, cursor2 = svc.query_failed_attempts(
        作业编号=JOB, 异常类型="缺标题", cursor=cursor1, size=1)
    assert [row["id"] for row in page2] == [scenario_records["b1"]]
    assert cursor2 is None

    # 另一作业的同地址失败被作业条件过滤掉；不按作业过滤时能看到
    j73_only, _ = svc.query_failed_attempts(作业编号=OTHER_JOB, 异常类型="缺标题")
    assert [row["id"] for row in j73_only] == [scenario_records["j73"]]
    all_rows, _ = svc.query_failed_attempts(异常类型="缺标题")
    assert scenario_records["j73"] in [row["id"] for row in all_rows]


# ---------------------------------------------------------------------------
# 主场景：夜班选中 → 白班补录 → 并发派工 → 恢复进程 → B2 重筛领取
# ---------------------------------------------------------------------------

def test_full_retry_dispatch_scenario(scenario_records: dict[str, int]) -> None:
    a1_id, b1_id = scenario_records["a1"], scenario_records["b1"]
    a1_fp = _attempt(a1_id)["输入指纹"]
    b1_fp = _attempt(b1_id)["输入指纹"]

    # 夜班：翻两页选中 A1、B1，组装重试候选清单交给采集任务
    page1, cursor1 = svc.query_failed_attempts(作业编号=JOB, 异常类型="缺标题", size=1)
    page2, _ = svc.query_failed_attempts(作业编号=JOB, 异常类型="缺标题", cursor=cursor1, size=1)
    selected = [page1[0]["id"], page2[0]["id"]]
    retry_list, message = svc.assemble_retry_list(作业编号=JOB, attempt_ids=selected)
    assert retry_list is not None, message
    assert retry_list["状态"] == "待领取"

    # 清单驱动采集任务的待执行列表
    pending = svc.job_pending_list(JOB)
    assert {item["失败尝试ID"] for item in pending} == {a1_id, b1_id}

    # 任务已领取清单，派工却停在进程启动前
    claimed, message = svc.claim_list(int(retry_list["id"]), 领取人="夜班采集任务")
    assert claimed is not None, message
    svc.pause_process(JOB)
    assert svc.process_state(JOB)["状态"] == "已暂停"
    assert _attempt(a1_id)["状态"] == "待派工"  # 已领未启动

    # 白班补录同一输入 A1 的成功回执
    receipt, message = svc.record_success(
        作业编号=JOB, 来源地址=ADDR, 输入内容=PAGE_A1, 回执来源="补录")
    assert receipt, message
    assert receipt["输入指纹"] == a1_fp

    # 迟到的旧派工与重送请求并发到达（进程仍未启动）
    with ThreadPoolExecutor(max_workers=2) as pool:
        late = pool.submit(svc.dispatch_list, int(retry_list["id"]), "旧派工")
        resend = pool.submit(svc.resend_list, int(retry_list["id"]))
        late.result()
        resend.result()
    # 进程未启动时：A1 已有成功回执被直接终态拦回；B1 仅暂态拦回，原因记回原失败尝试
    a1 = _attempt(a1_id)
    b1 = _attempt(b1_id)
    assert a1["状态"] == ATTEMPT_BLOCKED
    assert a1["未启动原因"] == REASON_SUCCESS_RECEIPT
    assert any(log["原因"] == REASON_SUCCESS_RECEIPT for log in a1["派工记录"])
    assert b1["状态"] == "待派工"
    assert b1["未启动原因"] == REASON_PROCESS_DOWN
    assert any(log["原因"] == REASON_PROCESS_DOWN for log in b1["派工记录"])

    # 恢复进程：已成功的 A1 不能再跑，B1 开跑且只开跑一次
    _, resumed = svc.resume_process(JOB)
    a1 = _attempt(a1_id)
    b1 = _attempt(b1_id)
    assert a1["状态"] == ATTEMPT_BLOCKED
    assert a1["未启动原因"] == REASON_SUCCESS_RECEIPT
    assert b1["状态"] == ATTEMPT_STARTED
    assert _starts_for(JOB, a1_fp) == [], "已补录成功的 A1 不应开跑"
    assert len(_starts_for(JOB, b1_fp)) == 1, "B1 应恰好开跑一次"
    assert any(r["结果"] == "启动" and r["触发来源"] == "进程恢复" for r in resumed)

    # 恢复后再有迟到的旧派工与重送并发到达：B1 不会开跑第二次
    with ThreadPoolExecutor(max_workers=2) as pool:
        again1 = pool.submit(svc.dispatch_list, int(retry_list["id"]), "旧派工")
        again2 = pool.submit(svc.resend_list, int(retry_list["id"]))
        again1.result()
        again2.result()
    assert len(_starts_for(JOB, b1_fp)) == 1, "并发重送不能让 B1 开跑两次"
    assert _attempt(a1_id)["状态"] == ATTEMPT_BLOCKED
    assert svc.list_retry_lists(作业编号=JOB)[0]["状态"] == LIST_DONE
    assert svc.job_pending_list(JOB) == [], "候选全部终态后待执行列表应清空"

    # 补录成功后仍能从原查询页定位失败尝试（不可再选）
    located, _ = svc.query_failed_attempts(作业编号=JOB, 异常类型="缺标题")
    located_a1 = next(row for row in located if row["id"] == a1_id)
    assert located_a1["状态"] == ATTEMPT_BLOCKED and located_a1["可选"] is False

    # 原规则、异常和成功回执分别可查
    detail = svc.get_failed_attempt(a1_id)
    assert detail is not None
    assert detail["解析规则"] == "title://html/head/title"
    assert detail["异常类型"] == "缺标题"
    assert detail["成功回执"] and detail["成功回执"][0]["回执来源"] == "补录"
    receipts = svc.list_success_receipts(作业编号=JOB, 来源地址=ADDR)
    assert any(row["输入指纹"] == a1_fp for row in receipts)

    # 给 B1 记成功（实采回执），B1 转已成功
    b1_receipt, message = svc.record_success(
        作业编号=JOB, 来源地址=ADDR, 输入内容=PAGE_B1, 回执来源="实采")
    assert b1_receipt, message
    assert _attempt(b1_id)["状态"] == ATTEMPT_SUCCEEDED

    # 同一地址换了一份输入 B2：新失败仍可选，旧回执管不到新输入
    b2 = ingest_local_page(作业编号=JOB, 来源地址=ADDR, page_path=FIXTURES / "page_b2.html")
    assert b2 is not None
    assert b2["输入指纹"] != b1_fp
    refiltered, _ = svc.query_failed_attempts(作业编号=JOB, 异常类型="缺标题")
    selectable = {row["id"]: row["可选"] for row in refiltered}
    assert selectable[b2["id"]] is True, "同地址新输入的失败应仍可选"
    assert selectable[a1_id] is False and selectable[b1_id] is False

    # 重新筛选并领取 B2，进程运行中直接派工开跑一次
    list2, message = svc.assemble_retry_list(作业编号=JOB, attempt_ids=[int(b2["id"])])
    assert list2 is not None, message
    claimed2, message = svc.claim_list(int(list2["id"]), 领取人="白班采集任务")
    assert claimed2 is not None, message
    results, _ = svc.dispatch_list(int(list2["id"]), "旧派工")
    assert results and results[0]["结果"] == "启动"
    assert len(_starts_for(JOB, b2["输入指纹"])) == 1
    assert _attempt(int(b2["id"]))["状态"] == ATTEMPT_STARTED


# ---------------------------------------------------------------------------
# 补充场景：同地址同输入已有成功回执时，派工一律拦回
# ---------------------------------------------------------------------------

def test_same_input_blocked_after_success() -> None:
    """同一输入已有成功回执：新清单派工被拦，未启动原因记回原失败尝试。"""
    job = "J81"
    attempt = svc.register_failed_attempt(
        作业编号=job, 来源地址="local://news.local/article/81",
        输入内容="输入快照-X", 异常类型="缺标题", 解析规则="title://html/head/title")
    svc.record_success(作业编号=job, 来源地址="local://news.local/article/81",
                       输入内容="输入快照-X", 回执来源="补录")
    # 已有成功回执的记录不再可选，组装清单直接被拒
    retry_list, message = svc.assemble_retry_list(作业编号=job, attempt_ids=[int(attempt["id"])])
    assert retry_list is None and "不可选" in message

    # 先组装领取再补录的，派工闸口拦回并记回原因
    attempt2 = svc.register_failed_attempt(
        作业编号=job, 来源地址="local://news.local/article/81",
        输入内容="输入快照-Y", 异常类型="缺标题")
    retry_list2, message = svc.assemble_retry_list(作业编号=job, attempt_ids=[int(attempt2["id"])])
    assert retry_list2 is not None, message
    svc.claim_list(int(retry_list2["id"]), 领取人="采集任务")
    svc.record_success(作业编号=job, 来源地址="local://news.local/article/81",
                       输入内容="输入快照-Y", 回执来源="补录")
    results, _ = svc.dispatch_list(int(retry_list2["id"]), "旧派工")
    assert results and results[0]["结果"] == "拦回"
    detail = svc.get_failed_attempt(int(attempt2["id"]))
    assert detail is not None
    assert detail["状态"] == ATTEMPT_BLOCKED
    assert detail["未启动原因"] == REASON_SUCCESS_RECEIPT
    assert _starts_for(job, detail["输入指纹"]) == []


# ---------------------------------------------------------------------------
# 补充场景：清单驱动采集任务待执行列表 + 清单状态流转
# ---------------------------------------------------------------------------

def test_pending_list_drives_crawl_job() -> None:
    job = "J82"
    first = svc.register_failed_attempt(
        作业编号=job, 来源地址="local://news.local/article/82",
        输入内容="输入快照-P", 异常类型="缺标题")
    second = svc.register_failed_attempt(
        作业编号=job, 来源地址="local://news.local/article/82",
        输入内容="输入快照-Q", 异常类型="字段缺失")
    retry_list, message = svc.assemble_retry_list(
        作业编号=job, attempt_ids=[int(first["id"]), int(second["id"])])
    assert retry_list is not None, message

    # 已入清单即进入采集任务待执行列表
    pending = svc.job_pending_list(job)
    assert {item["失败尝试ID"] for item in pending} == {first["id"], second["id"]}

    # 重复领取被拒
    svc.claim_list(int(retry_list["id"]), 领取人="采集任务")
    again, message = svc.claim_list(int(retry_list["id"]), 领取人="采集任务")
    assert again is None and "不能重复领取" in message

    # 进程默认运行中，派工后全部启动、清单完成、待执行列表清空
    results, _ = svc.dispatch_list(int(retry_list["id"]), "旧派工")
    assert results and all(item["结果"] == "启动" for item in results)
    assert svc.job_pending_list(job) == []
    assert svc.list_retry_lists(作业编号=job)[0]["状态"] == LIST_DONE
    assert _attempt(int(first["id"]))["状态"] == ATTEMPT_STARTED
    assert _attempt(int(second["id"]))["状态"] == ATTEMPT_STARTED


def test_new_attempt_defaults_open_and_selectable() -> None:
    """新登记的失败尝试默认待处理且可选。"""
    attempt = svc.register_failed_attempt(
        作业编号="J83", 来源地址="local://news.local/article/83",
        输入内容="输入快照-Z", 异常类型="缺标题")
    detail = svc.get_failed_attempt(int(attempt["id"]))
    assert detail is not None
    assert detail["状态"] == ATTEMPT_OPEN
    assert detail["可选"] is True
