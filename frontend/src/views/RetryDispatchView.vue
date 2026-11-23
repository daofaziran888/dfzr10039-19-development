<template>
  <section class="page" data-module="parse_log-retry">
    <header class="page-head">
      <div>
        <h2>失败尝试重试派工</h2>
        <p class="page-desc">
          从解析日志捞出缺标题等失败尝试，按作业和异常类型筛选、游标续读，
          选中后组装重试候选清单交给采集任务；派工被拦会把未启动原因记回原失败尝试。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="open-register" @click="showRegister = true">
          登记失败尝试
        </button>
      </div>
    </header>

    <p v-if="noticeMessage" class="notice-text" data-testid="notice">{{ noticeMessage }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <!-- 失败尝试查询：按作业和异常类型筛选，接着上次读到的继续读 -->
    <section class="panel">
      <h3>失败尝试查询</h3>
      <form class="filter-bar" @submit.prevent="search">
        <label class="filter-item">
          <span>作业编号</span>
          <input v-model.trim="filters.作业编号" data-testid="filter-job" placeholder="如 J72" />
        </label>
        <label class="filter-item">
          <span>异常类型</span>
          <input v-model.trim="filters.异常类型" data-testid="filter-exc" placeholder="如 缺标题" />
        </label>
        <button class="btn" data-testid="query" type="submit">查询</button>
        <button
          class="btn ghost"
          data-testid="next-page"
          type="button"
          :disabled="nextCursor === null"
          @click="loadMore"
        >
          继续读（接着上次）
        </button>
        <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
        <button
          class="btn primary"
          data-testid="submit-list"
          type="button"
          :disabled="!selected.size"
          @click="submitList"
        >
          提交选中为重试候选清单（{{ selected.size }}）
        </button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>选择</th><th>ID</th><th>作业编号</th><th>来源地址</th><th>输入摘要</th>
            <th>异常类型</th><th>解析规则</th><th>状态</th><th>未启动原因</th><th>可选</th><th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id" :data-testid="`attempt-${row.id}`" :data-status="row.状态">
            <td>
              <input
                type="checkbox"
                :checked="selected.has(row.id)"
                :disabled="!row.可选"
                :data-testid="`select-${row.id}`"
                @change="toggleSelect(row)"
              />
            </td>
            <td>{{ row.id }}</td>
            <td>{{ row.作业编号 }}</td>
            <td>{{ row.来源地址 }}</td>
            <td class="ellipsis" :title="row.输入摘要">{{ row.输入摘要 }}</td>
            <td>{{ row.异常类型 }}</td>
            <td>{{ row.解析规则 }}</td>
            <td>{{ row.状态 }}</td>
            <td>{{ row.未启动原因 || '—' }}</td>
            <td>{{ row.可选 ? '可选' : '不可选' }}</td>
            <td><button class="link" type="button" :data-testid="`detail-${row.id}`" @click="showDetail(row.id)">查看</button></td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="11" class="empty-state">暂无失败尝试，可先登记或调整筛选条件</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>已读 {{ rows.length }} 条失败尝试</span>
        <span>{{ nextCursor === null ? '已读完' : `下一游标 ${nextCursor}，可继续读` }}</span>
      </footer>
    </section>

    <!-- 重试候选清单：驱动采集任务待执行列表 -->
    <section class="panel">
      <h3>重试候选清单</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>清单编号</th><th>作业编号</th><th>状态</th><th>候选数</th>
            <th>领取人</th><th>重送次数</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="list in lists" :key="list.id" :data-testid="`list-${list.id}`">
            <td>{{ list.清单编号 }}</td>
            <td>{{ list.作业编号 }}</td>
            <td>{{ list.状态 }}</td>
            <td>{{ list.候选数 }}</td>
            <td>{{ list.领取人 || '—' }}</td>
            <td>{{ list.重送次数 }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`claim-${list.id}`" @click="claim(list)">领取</button>
              <button class="link" type="button" :data-testid="`dispatch-${list.id}`" @click="dispatch(list)">派工</button>
              <button class="link" type="button" :data-testid="`resend-${list.id}`" @click="resend(list)">重送清单</button>
            </td>
          </tr>
          <tr v-if="!lists.length">
            <td colspan="7" class="empty-state">暂无重试候选清单，先在上方选中失败尝试提交</td>
          </tr>
        </tbody>
      </table>

      <h4>采集任务待执行列表（{{ filters.作业编号 || '未指定作业' }}）</h4>
      <table class="data-table">
        <thead>
          <tr><th>清单编号</th><th>失败尝试ID</th><th>来源地址</th><th>输入指纹</th><th>异常类型</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in pending" :key="`${item.清单编号}-${item.失败尝试ID}`">
            <td>{{ item.清单编号 }}</td>
            <td>{{ item.失败尝试ID }}</td>
            <td>{{ item.来源地址 }}</td>
            <td>{{ item.输入指纹 }}</td>
            <td>{{ item.异常类型 }}</td>
            <td>{{ item.状态 }}</td>
          </tr>
          <tr v-if="!pending.length">
            <td colspan="6" class="empty-state">待执行列表为空</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 采集进程：暂停 / 恢复 -->
    <section class="panel">
      <h3>采集进程</h3>
      <div class="process-row">
        <span>作业 {{ filters.作业编号 || '—' }} 进程状态：<strong data-testid="process-state">{{ process?.状态 ?? '未查询' }}</strong></span>
        <button class="btn" type="button" data-testid="process-refresh" :disabled="!filters.作业编号" @click="refreshProcess">查询进程</button>
        <button class="btn" type="button" data-testid="process-pause" :disabled="!filters.作业编号" @click="pause">暂停进程</button>
        <button class="btn primary" type="button" data-testid="process-resume" :disabled="!filters.作业编号" @click="resume">恢复进程</button>
      </div>
    </section>

    <!-- 成功回执：补录与查询 -->
    <section class="panel">
      <h3>成功回执</h3>
      <form class="filter-bar" @submit.prevent="backfill">
        <label class="filter-item">
          <span>来源地址</span>
          <input v-model.trim="receiptForm.来源地址" data-testid="receipt-addr" placeholder="与失败尝试同地址" />
        </label>
        <label class="filter-item">
          <span>输入内容</span>
          <input v-model="receiptForm.输入内容" data-testid="receipt-input" placeholder="同一输入原文" />
        </label>
        <label class="filter-item">
          <span>回执来源</span>
          <input v-model.trim="receiptForm.回执来源" data-testid="receipt-source" placeholder="补录 / 实采" />
        </label>
        <button class="btn primary" data-testid="backfill" type="submit" :disabled="!filters.作业编号">补录成功回执</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>ID</th><th>作业编号</th><th>来源地址</th><th>输入指纹</th><th>回执来源</th><th>回执时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="receipt in receipts" :key="receipt.id">
            <td>{{ receipt.id }}</td>
            <td>{{ receipt.作业编号 }}</td>
            <td>{{ receipt.来源地址 }}</td>
            <td>{{ receipt.输入指纹 }}</td>
            <td>{{ receipt.回执来源 }}</td>
            <td>{{ receipt.回执时间 }}</td>
          </tr>
          <tr v-if="!receipts.length">
            <td colspan="6" class="empty-state">暂无成功回执</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 登记失败尝试 -->
    <div v-if="showRegister" class="modal-backdrop" data-testid="register-modal">
      <form class="modal" @submit.prevent="register">
        <h3>登记失败尝试</h3>
        <label class="create-field">
          <span>作业编号</span>
          <input v-model.trim="registerForm.作业编号" data-testid="register-job" required />
        </label>
        <label class="create-field">
          <span>来源地址</span>
          <input v-model.trim="registerForm.来源地址" data-testid="register-addr" required />
        </label>
        <label class="create-field">
          <span>输入内容</span>
          <textarea v-model="registerForm.输入内容" data-testid="register-input" required rows="4"></textarea>
        </label>
        <label class="create-field">
          <span>异常类型</span>
          <input v-model.trim="registerForm.异常类型" data-testid="register-exc" required />
        </label>
        <label class="create-field">
          <span>解析规则</span>
          <input v-model.trim="registerForm.解析规则" data-testid="register-rule" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showRegister = false">取消</button>
          <button class="btn primary" data-testid="register-submit" type="submit">确认登记</button>
        </div>
      </form>
    </div>

    <!-- 失败尝试详情：原规则、异常、派工记录、成功回执 -->
    <div v-if="detail" class="modal-backdrop" data-testid="detail-modal" @click.self="detail = null">
      <div class="modal">
        <h3>失败尝试 {{ detail.id }} 详情</h3>
        <dl class="detail-grid">
          <dt>作业编号</dt><dd>{{ detail.作业编号 }}</dd>
          <dt>来源地址</dt><dd>{{ detail.来源地址 }}</dd>
          <dt>输入指纹</dt><dd>{{ detail.输入指纹 }}</dd>
          <dt>异常类型</dt><dd>{{ detail.异常类型 }}</dd>
          <dt>解析规则</dt><dd>{{ detail.解析规则 || '—' }}</dd>
          <dt>错误详情</dt><dd>{{ detail.错误详情 || '—' }}</dd>
          <dt>状态</dt><dd>{{ detail.状态 }}</dd>
          <dt>未启动原因</dt><dd>{{ detail.未启动原因 || '—' }}</dd>
        </dl>
        <h4>派工记录</h4>
        <table class="data-table">
          <thead><tr><th>时间</th><th>来源</th><th>结果</th><th>原因</th></tr></thead>
          <tbody>
            <tr v-for="(log, index) in detail.派工记录" :key="index">
              <td>{{ log.时间 }}</td><td>{{ log.来源 }}</td><td>{{ log.结果 }}</td><td>{{ log.原因 }}</td>
            </tr>
            <tr v-if="!detail.派工记录.length"><td colspan="4" class="empty-state">暂无派工记录</td></tr>
          </tbody>
        </table>
        <h4>关联成功回执</h4>
        <table class="data-table">
          <thead><tr><th>输入指纹</th><th>回执来源</th><th>回执时间</th></tr></thead>
          <tbody>
            <tr v-for="receipt in detail.成功回执" :key="receipt.id">
              <td>{{ receipt.输入指纹 }}</td><td>{{ receipt.回执来源 }}</td><td>{{ receipt.回执时间 }}</td>
            </tr>
            <tr v-if="!detail.成功回执.length"><td colspan="3" class="empty-state">暂无成功回执</td></tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  assembleRetryList,
  claimRetryList,
  dispatchRetryList,
  getFailedAttempt,
  jobPendingList,
  listRetryLists,
  listSuccessReceipts,
  pauseProcess,
  processState,
  queryFailedAttempts,
  recordSuccess,
  registerFailedAttempt,
  resendRetryList,
  resumeProcess,
} from '@/api/retry-service'
import type {
  FailedAttempt,
  FailedAttemptDetail,
  PendingEntry,
  ProcessState,
  RetryList,
  SuccessReceipt,
} from '@/api/retry-service'

const PAGE_SIZE = 2

const filters = ref({ 作业编号: '', 异常类型: '' })
const rows = ref<FailedAttempt[]>([])
const nextCursor = ref<number | null>(null)
const selected = ref(new Set<number>())
const lists = ref<RetryList[]>([])
const pending = ref<PendingEntry[]>([])
const receipts = ref<SuccessReceipt[]>([])
const process = ref<ProcessState | null>(null)
const detail = ref<FailedAttemptDetail | null>(null)
const showRegister = ref(false)
const noticeMessage = ref('')
const errorMessage = ref('')

const receiptForm = ref({ 来源地址: '', 输入内容: '', 回执来源: '补录' })
const registerForm = ref({ 作业编号: '', 来源地址: '', 输入内容: '', 异常类型: '缺标题', 解析规则: '' })

function report(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
}

async function loadPage(cursor: number | null) {
  const payload = await queryFailedAttempts({
    作业编号: filters.value.作业编号 || undefined,
    异常类型: filters.value.异常类型 || undefined,
    cursor,
    size: PAGE_SIZE,
  })
  if (cursor === null) {
    rows.value = payload.items
  } else {
    rows.value = [...rows.value, ...payload.items]
  }
  nextCursor.value = payload.next_cursor
}

async function search() {
  errorMessage.value = ''
  selected.value.clear()
  try {
    await loadPage(null)
    await Promise.all([refreshLists(), refreshPending(), refreshProcess(), refreshReceipts()])
  } catch (error) {
    report(error, '失败尝试查询失败')
  }
}

async function loadMore() {
  errorMessage.value = ''
  try {
    await loadPage(nextCursor.value)
  } catch (error) {
    report(error, '继续读取失败')
  }
}

function resetFilters() {
  filters.value = { 作业编号: '', 异常类型: '' }
  void search()
}

function toggleSelect(row: FailedAttempt) {
  if (!row.可选) return
  if (selected.value.has(row.id)) {
    selected.value.delete(row.id)
  } else {
    selected.value.add(row.id)
  }
}

async function submitList() {
  errorMessage.value = ''
  noticeMessage.value = ''
  if (!filters.value.作业编号) {
    errorMessage.value = '先按作业编号筛选，再把选中记录组装成该作业的重试候选清单'
    return
  }
  try {
    const result = await assembleRetryList(filters.value.作业编号, [...selected.value])
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    selected.value.clear()
    await search()
  } catch (error) {
    report(error, '提交重试候选清单失败')
  }
}

async function refreshLists() {
  const payload = await listRetryLists(filters.value.作业编号 || undefined)
  lists.value = payload.items
}

async function refreshPending() {
  if (!filters.value.作业编号) {
    pending.value = []
    return
  }
  const payload = await jobPendingList(filters.value.作业编号)
  pending.value = payload.items
}

async function refreshProcess() {
  if (!filters.value.作业编号) {
    process.value = null
    return
  }
  process.value = await processState(filters.value.作业编号)
}

async function refreshReceipts() {
  const payload = await listSuccessReceipts({ 作业编号: filters.value.作业编号 || undefined })
  receipts.value = payload.items
}

async function runAndRefresh(action: () => Promise<{ ok: boolean; message: string }>, fallback: string) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await action()
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await search()
  } catch (error) {
    report(error, fallback)
  }
}

async function claim(list: RetryList) {
  await runAndRefresh(() => claimRetryList(list.id, '值班采集任务'), '领取清单失败')
}

async function dispatch(list: RetryList) {
  await runAndRefresh(() => dispatchRetryList(list.id), '派工失败')
}

async function resend(list: RetryList) {
  await runAndRefresh(() => resendRetryList(list.id), '重送清单失败')
}

async function pause() {
  await runAndRefresh(() => pauseProcess(filters.value.作业编号), '暂停进程失败')
}

async function resume() {
  await runAndRefresh(() => resumeProcess(filters.value.作业编号), '恢复进程失败')
}

async function backfill() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await recordSuccess({
      作业编号: filters.value.作业编号,
      来源地址: receiptForm.value.来源地址,
      输入内容: receiptForm.value.输入内容,
      回执来源: receiptForm.value.回执来源 || '补录',
    })
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    receiptForm.value = { 来源地址: '', 输入内容: '', 回执来源: '补录' }
    await search()
  } catch (error) {
    report(error, '补录成功回执失败')
  }
}

async function register() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await registerFailedAttempt(registerForm.value)
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    showRegister.value = false
    registerForm.value = { 作业编号: '', 来源地址: '', 输入内容: '', 异常类型: '缺标题', 解析规则: '' }
    await search()
  } catch (error) {
    report(error, '登记失败尝试失败')
  }
}

async function showDetail(id: number) {
  errorMessage.value = ''
  try {
    detail.value = await getFailedAttempt(id)
  } catch (error) {
    report(error, '失败尝试详情读取失败')
  }
}

onMounted(search)
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
}
.panel h3 { margin: 0 0 10px; font-size: 15px; }
.panel h4 { margin: 14px 0 8px; font-size: 13px; color: var(--muted); }
.notice-text { color: #15803d; font-size: 13px; }
.ellipsis {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.process-row { display: flex; gap: 10px; align-items: center; font-size: 13px; }
.detail-grid {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 6px 12px;
  font-size: 13px;
}
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; word-break: break-all; }
.create-field textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  font-family: inherit;
}
</style>
