/** 失败尝试重试派工接口封装：与后端 /api/retry/* 一一对应。 */
import { request } from './client'

export type DispatchEvent = {
  时间: string
  来源: string
  结果: string
  原因: string
}

export type FailedAttempt = {
  id: number
  作业编号: string
  来源地址: string
  输入指纹: string
  输入摘要: string
  异常类型: string
  解析规则: string
  错误详情: string
  记录时间: string
  状态: string
  未启动原因: string
  清单编号: string
  派工记录: DispatchEvent[]
  可选: boolean
}

export type FailedAttemptDetail = FailedAttempt & {
  成功回执: SuccessReceipt[]
  启动记录: DispatchStart[]
}

export type DispatchStart = {
  id: number
  作业编号: string
  来源地址: string
  输入指纹: string
  清单编号: string
  进程ID: string
  触发来源: string
  启动时间: string
}

export type SuccessReceipt = {
  id: number
  作业编号: string
  来源地址: string
  输入指纹: string
  回执来源: string
  回执时间: string
}

export type RetryList = {
  id: number
  清单编号: string
  作业编号: string
  状态: string
  候选IDs: number[]
  候选数: number
  领取人: string
  领取时间: string
  重送次数: number
  创建时间: string
}

export type PendingEntry = {
  清单编号: string
  失败尝试ID: number
  来源地址: string
  输入指纹: string
  异常类型: string
  状态: string
}

export type DispatchResult = {
  失败尝试ID: number
  来源地址: string
  输入指纹: string
  触发来源: string
  结果: string
  原因: string
}

export type ProcessState = {
  id: number
  作业编号: string
  进程ID: string
  状态: string
}

export type ActionReply = {
  ok: boolean
  message: string
  entry?: unknown
  items?: DispatchResult[]
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `接口返回 ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // 保留默认提示
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function queryFailedAttempts(params: {
  作业编号?: string
  异常类型?: string
  cursor?: number | null
  size?: number
}): Promise<{ items: FailedAttempt[]; next_cursor: number | null }> {
  const query = new URLSearchParams()
  if (params.作业编号) query.set('作业编号', params.作业编号)
  if (params.异常类型) query.set('异常类型', params.异常类型)
  if (params.cursor !== null && params.cursor !== undefined) query.set('cursor', String(params.cursor))
  query.set('size', String(params.size ?? 5))
  return parseJson(await request(`/api/retry/failed-attempts?${query.toString()}`))
}

export async function getFailedAttempt(id: number): Promise<FailedAttemptDetail> {
  return parseJson(await request(`/api/retry/failed-attempts/${id}`))
}

export async function registerFailedAttempt(values: {
  作业编号: string
  来源地址: string
  输入内容: string
  异常类型: string
  解析规则?: string
  错误详情?: string
}): Promise<ActionReply> {
  return parseJson(
    await request('/api/retry/failed-attempts', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  )
}

export async function assembleRetryList(作业编号: string, attemptIds: number[]): Promise<ActionReply> {
  return parseJson(
    await request('/api/retry/lists', {
      method: 'POST',
      body: JSON.stringify({ 作业编号, attempt_ids: attemptIds }),
    }),
  )
}

export async function listRetryLists(作业编号?: string): Promise<{ items: RetryList[] }> {
  const query = new URLSearchParams()
  if (作业编号) query.set('作业编号', 作业编号)
  return parseJson(await request(`/api/retry/lists?${query.toString()}`))
}

export async function claimRetryList(listId: number, 领取人: string): Promise<ActionReply> {
  return parseJson(
    await request(`/api/retry/lists/${listId}/claim`, {
      method: 'POST',
      body: JSON.stringify({ 领取人 }),
    }),
  )
}

export async function dispatchRetryList(listId: number): Promise<ActionReply> {
  return parseJson(await request(`/api/retry/lists/${listId}/dispatch`, { method: 'POST' }))
}

export async function resendRetryList(listId: number): Promise<ActionReply> {
  return parseJson(await request(`/api/retry/lists/${listId}/resend`, { method: 'POST' }))
}

export async function jobPendingList(作业编号: string): Promise<{ items: PendingEntry[] }> {
  return parseJson(await request(`/api/retry/pending/${encodeURIComponent(作业编号)}`))
}

export async function processState(作业编号: string): Promise<ProcessState> {
  return parseJson(await request(`/api/retry/process/${encodeURIComponent(作业编号)}`))
}

export async function pauseProcess(作业编号: string): Promise<ActionReply> {
  return parseJson(
    await request(`/api/retry/process/${encodeURIComponent(作业编号)}/pause`, { method: 'POST' }),
  )
}

export async function resumeProcess(作业编号: string): Promise<ActionReply> {
  return parseJson(
    await request(`/api/retry/process/${encodeURIComponent(作业编号)}/resume`, { method: 'POST' }),
  )
}

export async function recordSuccess(values: {
  作业编号: string
  来源地址: string
  输入内容?: string
  输入指纹?: string
  回执来源?: string
}): Promise<ActionReply> {
  return parseJson(
    await request('/api/retry/success-receipts', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  )
}

export async function listSuccessReceipts(params: {
  作业编号?: string
  来源地址?: string
}): Promise<{ items: SuccessReceipt[] }> {
  const query = new URLSearchParams()
  if (params.作业编号) query.set('作业编号', params.作业编号)
  if (params.来源地址) query.set('来源地址', params.来源地址)
  return parseJson(await request(`/api/retry/success-receipts?${query.toString()}`))
}
