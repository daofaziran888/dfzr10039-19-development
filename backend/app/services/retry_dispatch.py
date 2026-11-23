"""失败尝试重试派工领域服务。

补齐解析日志缺少的入口：把缺标题等失败尝试按作业和异常类型查出来，
组装成重试候选清单交给采集任务，由清单驱动采集任务的待执行列表。

关键约束（对应夜班/白班交接场景）：
- 派工闸口原子化：同一份输入（作业+地址+输入指纹）已有成功回执的，绝不再跑；
- 同一输入只允许开跑一次：迟到的旧派工与重送并发到达也只放行一次；
- 派工被拦时把未启动原因记回原失败尝试，原查询页仍能定位它；
- 同一地址换了一份输入产生的新失败是新记录，不受影响、仍可选。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Any

from app.store import store

# 失败尝试状态机
ATTEMPT_OPEN = "待处理"        # 刚登记，可选
ATTEMPT_LISTED = "已入清单"    # 已组装进重试候选清单
ATTEMPT_PENDING = "待派工"     # 清单已被任务领取，等待派工
ATTEMPT_STARTED = "已启动"     # 进程已领取实际采集入口并开跑
ATTEMPT_BLOCKED = "已拦回"     # 派工被拦（终态），未启动原因已记回
ATTEMPT_SUCCEEDED = "已成功"   # 已有成功回执（实采或补录）
TERMINAL_STATES = {ATTEMPT_STARTED, ATTEMPT_BLOCKED, ATTEMPT_SUCCEEDED}

# 清单状态机
LIST_OPEN = "待领取"
LIST_CLAIMED = "已领取"
LIST_DONE = "已完成"

# 未启动原因（派工被拦时记回原失败尝试）
REASON_SUCCESS_RECEIPT = "已存在成功回执，禁止重复采集"
REASON_DUPLICATE_DISPATCH = "重复派工已拦截，避免重复开跑"
REASON_PROCESS_DOWN = "采集进程未启动"

# 采集进程状态
PROCESS_RUNNING = "运行中"
PROCESS_PAUSED = "已暂停"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def input_fingerprint(input_content: str) -> str:
    """输入指纹：同地址换一份输入就是新指纹，旧成功回执管不到它。"""
    return hashlib.sha1(input_content.encode("utf-8")).hexdigest()[:12]


class RetryDispatchService:
    """失败尝试查询、重试候选清单、派工闸口和成功回执的领域服务。"""

    def __init__(self) -> None:
        # 派工闸口互斥锁：旧派工与重送并发到达时，检查+放行必须原子完成
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 数据表
    # ------------------------------------------------------------------

    def _attempts(self) -> list[dict[str, Any]]:
        return store.rows("failed_attempt")

    def _receipts(self) -> list[dict[str, Any]]:
        return store.rows("success_receipt")

    def _lists(self) -> list[dict[str, Any]]:
        return store.rows("retry_list")

    def _starts(self) -> list[dict[str, Any]]:
        return store.rows("dispatch_start")

    def _processes(self) -> list[dict[str, Any]]:
        return store.rows("crawl_process")

    @staticmethod
    def _next_id(rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    @staticmethod
    def _key_of(row: dict[str, Any]) -> tuple[str, str, str]:
        """去重键：作业 + 地址 + 输入指纹。同一地址换输入即新键。"""
        return (str(row.get("作业编号", "")), str(row.get("来源地址", "")),
                str(row.get("输入指纹", "")))

    # ------------------------------------------------------------------
    # 失败尝试登记与游标查询
    # ------------------------------------------------------------------

    def register_failed_attempt(
        self,
        *,
        作业编号: str,
        来源地址: str,
        输入内容: str,
        异常类型: str,
        解析规则: str = "",
        错误详情: str = "",
    ) -> dict[str, Any]:
        """登记一条失败尝试（本地网页/日志录入也走这里）。"""
        with self._lock:
            row = {
                "id": self._next_id(self._attempts()),
                "作业编号": 作业编号,
                "来源地址": 来源地址,
                "输入指纹": input_fingerprint(输入内容),
                "输入摘要": 输入内容.strip()[:24],
                "异常类型": 异常类型,
                "解析规则": 解析规则,
                "错误详情": 错误详情,
                "记录时间": _now(),
                "状态": ATTEMPT_OPEN,
                "未启动原因": "",
                "清单编号": "",
                "派工记录": [],
            }
            self._attempts().append(row)
            return row

    def query_failed_attempts(
        self,
        *,
        作业编号: str | None = None,
        异常类型: str | None = None,
        cursor: int | None = None,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """按作业和异常类型筛选失败尝试，游标续读。

        cursor 是上次读到的最后一条 id；传入后接着往后读，
        返回 (本页记录, 下一游标)，没有更多时下一游标为 None。
        每条记录带「可选」标记：补录过成功回执的旧记录仍能定位，但不可再选。
        """
        rows = sorted(self._attempts(), key=lambda row: int(row.get("id", 0)))
        if 作业编号:
            rows = [row for row in rows if row.get("作业编号") == 作业编号]
        if 异常类型:
            rows = [row for row in rows if row.get("异常类型") == 异常类型]
        if cursor is not None:
            rows = [row for row in rows if int(row.get("id", 0)) > cursor]
        page = rows[:size]
        has_more = len(rows) > len(page)
        items = [dict(row, 可选=self._selectable(row)) for row in page]
        next_cursor = int(page[-1]["id"]) if page and has_more else None
        return items, next_cursor

    def get_failed_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        """失败尝试详情：原规则、异常、派工记录和关联成功回执一起返回。"""
        for row in self._attempts():
            if int(row.get("id", 0)) == attempt_id:
                key = self._key_of(row)
                receipts = [r for r in self._receipts() if self._key_of(r) == key]
                starts = [s for s in self._starts() if self._key_of(s) == key]
                return dict(row, 可选=self._selectable(row),
                            成功回执=receipts, 启动记录=starts)
        return None

    def _selectable(self, row: dict[str, Any]) -> bool:
        if row.get("状态") != ATTEMPT_OPEN:
            return False
        return not self._has_receipt(self._key_of(row))

    # ------------------------------------------------------------------
    # 重试候选清单：组装、领取、驱动采集任务待执行列表
    # ------------------------------------------------------------------

    def assemble_retry_list(
        self, *, 作业编号: str, attempt_ids: list[int],
    ) -> tuple[dict[str, Any] | None, str]:
        """把选中的失败尝试组装成重试候选清单，交给采集任务。"""
        with self._lock:
            picked: list[dict[str, Any]] = []
            for attempt_id in attempt_ids:
                row = self._find_attempt(attempt_id)
                if row is None:
                    return None, f"失败尝试 {attempt_id} 不存在"
                if row.get("作业编号") != 作业编号:
                    return None, f"失败尝试 {attempt_id} 不属于作业 {作业编号}"
                if not self._selectable(row):
                    return None, f"失败尝试 {attempt_id} 当前不可选（{row.get('状态')}）"
                picked.append(row)
            if not picked:
                return None, "未选中任何失败尝试"
            list_row = {
                "id": self._next_id(self._lists()),
                "清单编号": f"RETRY-{作业编号}-{self._next_id(self._lists()):04d}",
                "作业编号": 作业编号,
                "状态": LIST_OPEN,
                "候选IDs": [int(row["id"]) for row in picked],
                "领取人": "",
                "领取时间": "",
                "重送次数": 0,
                "创建时间": _now(),
            }
            self._lists().append(list_row)
            for row in picked:
                row["状态"] = ATTEMPT_LISTED
                row["清单编号"] = list_row["清单编号"]
            return list_row, f"重试候选清单 {list_row['清单编号']} 已组装，共 {len(picked)} 条"

    def claim_list(self, list_id: int, *, 领取人: str) -> tuple[dict[str, Any] | None, str]:
        """采集任务领取清单：候选进入待派工，驱动作业的待执行列表。"""
        with self._lock:
            list_row = self._find_list(list_id)
            if list_row is None:
                return None, f"重试候选清单 {list_id} 不存在"
            if list_row.get("状态") != LIST_OPEN:
                return None, f"清单当前状态为{list_row.get('状态')}，不能重复领取"
            list_row["状态"] = LIST_CLAIMED
            list_row["领取人"] = 领取人
            list_row["领取时间"] = _now()
            for row in self._list_attempts(list_row):
                if row.get("状态") == ATTEMPT_LISTED:
                    row["状态"] = ATTEMPT_PENDING
            return list_row, f"清单 {list_row['清单编号']} 已被{领取人}领取"

    def job_pending_list(self, 作业编号: str) -> list[dict[str, Any]]:
        """采集任务的待执行列表：由该作业所有清单里未终态的候选驱动。"""
        pending: list[dict[str, Any]] = []
        for list_row in self._lists():
            if list_row.get("作业编号") != 作业编号:
                continue
            for row in self._list_attempts(list_row):
                if row.get("状态") in (ATTEMPT_LISTED, ATTEMPT_PENDING):
                    pending.append({
                        "清单编号": list_row["清单编号"],
                        "失败尝试ID": row["id"],
                        "来源地址": row["来源地址"],
                        "输入指纹": row["输入指纹"],
                        "异常类型": row["异常类型"],
                        "状态": row["状态"],
                    })
        return pending

    def list_retry_lists(self, *, 作业编号: str | None = None) -> list[dict[str, Any]]:
        rows = self._lists()
        if 作业编号:
            rows = [row for row in rows if row.get("作业编号") == 作业编号]
        return [dict(row, 候选数=len(row.get("候选IDs", []))) for row in rows]

    # ------------------------------------------------------------------
    # 派工闸口：旧派工 / 重送 / 进程恢复都从这里过
    # ------------------------------------------------------------------

    def dispatch_list(self, list_id: int, 来源: str = "旧派工") -> tuple[list[dict[str, Any]] | None, str]:
        """对清单内待派工候选逐个过闸口；并发安全、可重入。"""
        list_row = self._find_list(list_id)
        if list_row is None:
            return None, f"重试候选清单 {list_id} 不存在"
        results = [
            self._gate_start(attempt, list_row, 来源)
            for attempt in self._list_attempts(list_row)
        ]
        with self._lock:
            self._refresh_list_status(list_row)
        return results, f"清单 {list_row['清单编号']} 派工完成（{来源}）"

    def resend_list(self, list_id: int) -> tuple[list[dict[str, Any]] | None, str]:
        """重送清单：与迟到的旧派工等价，走同一个原子闸口。"""
        list_row = self._find_list(list_id)
        if list_row is None:
            return None, f"重试候选清单 {list_id} 不存在"
        with self._lock:
            list_row["重送次数"] = int(list_row.get("重送次数", 0)) + 1
        return self.dispatch_list(list_id, 来源="重送")

    def _gate_start(
        self, attempt: dict[str, Any], list_row: dict[str, Any], 来源: str,
    ) -> dict[str, Any]:
        """单条候选的派工闸口（原子）：成功回执拦截 > 重复开跑拦截 > 进程状态。"""
        with self._lock:
            状态 = attempt.get("状态")
            if 状态 != ATTEMPT_PENDING:
                # 已被其它并发派工处理过（已启动/已拦回/已成功），幂等跳过
                return self._result(attempt, 来源, "跳过", f"当前状态{状态}，无需重复派工")
            key = self._key_of(attempt)
            if self._has_receipt(key):
                attempt["状态"] = ATTEMPT_BLOCKED
                attempt["未启动原因"] = REASON_SUCCESS_RECEIPT
                self._log_dispatch(attempt, 来源, "拦回", REASON_SUCCESS_RECEIPT)
                return self._result(attempt, 来源, "拦回", REASON_SUCCESS_RECEIPT)
            if self._has_start(key):
                attempt["状态"] = ATTEMPT_BLOCKED
                attempt["未启动原因"] = REASON_DUPLICATE_DISPATCH
                self._log_dispatch(attempt, 来源, "拦回", REASON_DUPLICATE_DISPATCH)
                return self._result(attempt, 来源, "拦回", REASON_DUPLICATE_DISPATCH)
            process = self._process_row(str(attempt.get("作业编号")))
            if process is not None and process.get("状态") == PROCESS_PAUSED:
                # 暂态拦回：记回未启动原因，候选保持待派工，进程恢复后再放
                attempt["未启动原因"] = REASON_PROCESS_DOWN
                self._log_dispatch(attempt, 来源, "拦回", REASON_PROCESS_DOWN)
                return self._result(attempt, 来源, "拦回", REASON_PROCESS_DOWN)
            # 放行：进程领取实际采集入口，登记启动记录保证只开跑一次
            self._starts().append({
                "id": self._next_id(self._starts()),
                "作业编号": attempt["作业编号"],
                "来源地址": attempt["来源地址"],
                "输入指纹": attempt["输入指纹"],
                "清单编号": list_row["清单编号"],
                "进程ID": self._process_id(str(attempt["作业编号"])),
                "触发来源": 来源,
                "启动时间": _now(),
            })
            attempt["状态"] = ATTEMPT_STARTED
            attempt["未启动原因"] = ""
            self._log_dispatch(attempt, 来源, "启动", "进程已领取采集入口")
            return self._result(attempt, 来源, "启动", "进程已领取采集入口")

    @staticmethod
    def _result(attempt: dict[str, Any], 来源: str, 结果: str, 原因: str) -> dict[str, Any]:
        return {
            "失败尝试ID": attempt.get("id"),
            "来源地址": attempt.get("来源地址"),
            "输入指纹": attempt.get("输入指纹"),
            "触发来源": 来源,
            "结果": 结果,
            "原因": 原因,
        }

    def _log_dispatch(self, attempt: dict[str, Any], 来源: str, 结果: str, 原因: str) -> None:
        attempt.setdefault("派工记录", []).append({
            "时间": _now(), "来源": 来源, "结果": 结果, "原因": 原因,
        })

    def _refresh_list_status(self, list_row: dict[str, Any]) -> None:
        attempts = self._list_attempts(list_row)
        if attempts and all(row.get("状态") in TERMINAL_STATES for row in attempts):
            list_row["状态"] = LIST_DONE

    # ------------------------------------------------------------------
    # 采集进程：暂停 / 恢复（恢复时冲刷该作业所有待派工候选）
    # ------------------------------------------------------------------

    def pause_process(self, 作业编号: str) -> dict[str, Any]:
        with self._lock:
            process = self._ensure_process(作业编号)
            process["状态"] = PROCESS_PAUSED
            return dict(process)

    def resume_process(self, 作业编号: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """恢复进程，并对该作业已领取清单冲刷一轮派工。"""
        with self._lock:
            process = self._ensure_process(作业编号)
            process["状态"] = PROCESS_RUNNING
        results: list[dict[str, Any]] = []
        for list_row in self._lists():
            if list_row.get("作业编号") == 作业编号 and list_row.get("状态") == LIST_CLAIMED:
                dispatched, _ = self.dispatch_list(int(list_row["id"]), 来源="进程恢复")
                results.extend(dispatched or [])
        return dict(process), results

    def process_state(self, 作业编号: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._ensure_process(作业编号))

    def _ensure_process(self, 作业编号: str) -> dict[str, Any]:
        process = self._process_row(作业编号)
        if process is None:
            process = {
                "id": self._next_id(self._processes()),
                "作业编号": 作业编号,
                "进程ID": f"PROC-{作业编号}",
                "状态": PROCESS_RUNNING,
            }
            self._processes().append(process)
        return process

    def _process_row(self, 作业编号: str) -> dict[str, Any] | None:
        for row in self._processes():
            if row.get("作业编号") == 作业编号:
                return row
        return None

    def _process_id(self, 作业编号: str) -> str:
        process = self._process_row(作业编号)
        return str(process.get("进程ID")) if process else f"PROC-{作业编号}"

    # ------------------------------------------------------------------
    # 成功回执：补录 / 实采登记，单独可查
    # ------------------------------------------------------------------

    def record_success(
        self,
        *,
        作业编号: str,
        来源地址: str,
        输入内容: str | None = None,
        输入指纹: str | None = None,
        回执来源: str = "补录",
    ) -> tuple[dict[str, Any], str]:
        """登记成功回执（补录或实采）。同一输入幂等，重复补录不产生第二条。"""
        指纹 = 输入指纹 or (input_fingerprint(输入内容) if 输入内容 is not None else "")
        if not 指纹:
            return {}, "缺少输入内容或输入指纹，无法登记成功回执"
        key = (作业编号, 来源地址, 指纹)
        with self._lock:
            for receipt in self._receipts():
                if self._key_of(receipt) == key:
                    return receipt, f"作业 {作业编号} 该输入已有成功回执，无需重复登记"
            receipt = {
                "id": self._next_id(self._receipts()),
                "作业编号": 作业编号,
                "来源地址": 来源地址,
                "输入指纹": 指纹,
                "回执来源": 回执来源,
                "回执时间": _now(),
            }
            self._receipts().append(receipt)
            # 已启动的候选收到实采回执即转已成功；待派工的留给派工闸口拦截记回
            for attempt in self._attempts():
                if self._key_of(attempt) == key and attempt.get("状态") == ATTEMPT_STARTED:
                    attempt["状态"] = ATTEMPT_SUCCEEDED
            return receipt, f"作业 {作业编号} 成功回执已登记（{回执来源}）"

    def list_success_receipts(
        self, *, 作业编号: str | None = None, 来源地址: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._receipts()
        if 作业编号:
            rows = [row for row in rows if row.get("作业编号") == 作业编号]
        if 来源地址:
            rows = [row for row in rows if row.get("来源地址") == 来源地址]
        return list(rows)

    # ------------------------------------------------------------------
    # 内部查询
    # ------------------------------------------------------------------

    def _has_receipt(self, key: tuple[str, str, str]) -> bool:
        return any(self._key_of(row) == key for row in self._receipts())

    def _has_start(self, key: tuple[str, str, str]) -> bool:
        return any(self._key_of(row) == key for row in self._starts())

    def _find_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        for row in self._attempts():
            if int(row.get("id", 0)) == int(attempt_id):
                return row
        return None

    def _find_list(self, list_id: int) -> dict[str, Any] | None:
        for row in self._lists():
            if int(row.get("id", 0)) == int(list_id):
                return row
        return None

    def _list_attempts(self, list_row: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            row for attempt_id in list_row.get("候选IDs", [])
            if (row := self._find_attempt(int(attempt_id))) is not None
        ]


retry_dispatch_service = RetryDispatchService()
