<template>
  <div>
    <!-- Top Action Bar -->
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px">
      <div style="font-size: 16px; font-weight: 600; color: #303133">
        🖥️ 实时健康监控
      </div>
      <div style="display: flex; align-items: center; gap: 10px">
        <span style="font-size: 12px; color: #909399">自动刷新:</span>
        <el-radio-group v-model="autoRefreshInterval" size="small" @change="setupAutoRefresh">
          <el-radio-button :value="0">关闭</el-radio-button>
          <el-radio-button :value="15">15秒</el-radio-button>
          <el-radio-button :value="30">30秒</el-radio-button>
          <el-radio-button :value="60">60秒</el-radio-button>
        </el-radio-group>
        <el-button size="small" type="primary" plain :loading="refreshing" @click="manualRefresh">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </div>
    </div>

    <!-- Stats Cards -->
    <el-row :gutter="16" style="margin-bottom: 20px">
      <el-col :span="4" v-for="card in cards" :key="card.label">
        <el-card shadow="hover" style="text-align: center">
          <div style="font-size: 28px; font-weight: 700" :style="{ color: card.color }">
            {{ card.value }}
          </div>
          <div style="color: #999; margin-top: 4px">{{ card.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Group Summary -->
    <el-card style="margin-bottom: 20px">
      <template #header>
        <span style="font-weight: 600">📊 分组汇总</span>
      </template>
      <el-table :data="groupStats" stripe size="small" style="width: 100%"
        show-summary :summary-method="groupSummary"
        highlight-current-row @current-change="onGroupSelect">
        <el-table-column prop="group" label="分组" min-width="140">
          <template #default="{ row }">
            <router-link :to="`/targets?group=${encodeURIComponent(row.group)}`" style="color: #409eff; text-decoration: none; font-weight: 500">
              {{ row.group }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="total" label="总数" width="80" align="center" />
        <el-table-column prop="enabled" label="启用" width="80" align="center" />
        <el-table-column label="健康" width="80" align="center">
          <template #default="{ row }">
            <span style="color: #67c23a; font-weight: 600">{{ row.healthy }}</span>
          </template>
        </el-table-column>
        <el-table-column label="异常" width="80" align="center">
          <template #default="{ row }">
            <span :style="{ color: row.unhealthy > 0 ? '#f56c6c' : '#999', fontWeight: row.unhealthy > 0 ? '600' : '400' }">
              {{ row.unhealthy }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="未检测" width="80" align="center">
          <template #default="{ row }">
            <span style="color: #909399">{{ row.unknown }}</span>
          </template>
        </el-table-column>
        <el-table-column label="渲染异常" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.render_anomaly > 0" type="warning" size="small">{{ row.render_anomaly }}</el-tag>
            <span v-else style="color: #999">0</span>
          </template>
        </el-table-column>
        <el-table-column label="🔒 证书" width="140" align="center">
          <template #default="{ row }">
            <span v-if="!row.ssl_checked && !row.ssl_no_https" style="color: #ccc">未检</span>
            <span v-else style="font-size: 12px">
              <span v-if="row.ssl_ok" style="color: #67c23a; font-weight: 600">{{ row.ssl_ok }}正常</span>
              <span v-if="row.ssl_error > 0" style="color: #f56c6c; font-weight: 600; margin-left: 3px">{{ row.ssl_error }}异常</span>
              <span v-if="row.ssl_expiring_soon > 0" style="color: #e6a23c; font-weight: 600; margin-left: 3px">{{ row.ssl_expiring_soon }}将过期</span>
              <span v-if="row.ssl_no_https > 0" style="color: #909399; margin-left: 3px">{{ row.ssl_no_https }}HTTP</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="可用率" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.uptime_pct !== null"
              :style="{ color: row.uptime_pct < 95 ? '#f56c6c' : row.uptime_pct < 99 ? '#e6a23c' : '#67c23a', fontWeight: 700 }">
              {{ row.uptime_pct }}%
            </span>
            <span v-else style="color: #999">-</span>
          </template>
        </el-table-column>
        <el-table-column label="平均延迟" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.avg_latency" :style="{ color: row.avg_latency > 3000 ? '#f56c6c' : row.avg_latency > 1000 ? '#e6a23c' : '#67c23a' }">
              {{ row.avg_latency }}ms
            </span>
            <span v-else style="color: #999">-</span>
          </template>
        </el-table-column>
        <el-table-column label="最大连续失败" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.max_consecutive_fails > 0" type="danger" size="small">{{ row.max_consecutive_fails }}</el-tag>
            <span v-else style="color: #999">0</span>
          </template>
        </el-table-column>
        <el-table-column label="状态分布" min-width="180">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 4px; height: 20px">
              <div v-if="row.healthy > 0"
                :style="{ width: pct(row.healthy, row.enabled) + '%', background: '#67c23a', height: '16px', borderRadius: '3px', minWidth: '4px' }"
                :title="`健康: ${row.healthy}`" />
              <div v-if="row.unhealthy > 0"
                :style="{ width: pct(row.unhealthy, row.enabled) + '%', background: '#f56c6c', height: '16px', borderRadius: '3px', minWidth: '4px' }"
                :title="`异常: ${row.unhealthy}`" />
              <div v-if="row.unknown > 0"
                :style="{ width: pct(row.unknown, row.enabled) + '%', background: '#dcdfe6', height: '16px', borderRadius: '3px', minWidth: '4px' }"
                :title="`未知: ${row.unknown}`" />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="checkGroup(row.group)">
              <el-icon><Refresh /></el-icon> 检测此组
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- SSL Certificate Overview -->
    <el-card style="margin-bottom: 20px" v-if="sslCounts.total">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
          <span style="font-weight: 600">🔒 SSL 证书状态
            <el-tag v-if="sslCounts.invalid" type="danger" size="small" style="margin-left: 6px">{{ sslCounts.invalid }} 异常</el-tag>
            <el-tag v-if="sslCounts.expiring" type="warning" size="small" style="margin-left: 6px">{{ sslCounts.expiring }} 即将过期</el-tag>
            <el-tag v-if="sslCounts.noHttps" size="small" style="margin-left: 6px">{{ sslCounts.noHttps }} HTTP未加密</el-tag>
            <el-tag v-if="sslCounts.valid" type="success" size="small" style="margin-left: 6px">{{ sslCounts.valid }} 正常</el-tag>
          </span>
          <div style="display: flex; align-items: center; gap: 8px">
            <el-radio-group v-model="sslFilter" size="small" @change="loadSslData">
              <el-radio-button value="all">全部 ({{ sslCounts.total }})</el-radio-button>
              <el-radio-button value="invalid" :disabled="!sslCounts.invalid">异常 ({{ sslCounts.invalid }})</el-radio-button>
              <el-radio-button value="expiring" :disabled="!sslCounts.expiring">即将过期 ({{ sslCounts.expiring }})</el-radio-button>
              <el-radio-button value="no_https" :disabled="!sslCounts.noHttps">HTTP未加密 ({{ sslCounts.noHttps }})</el-radio-button>
              <el-radio-button value="valid" :disabled="!sslCounts.valid">正常 ({{ sslCounts.valid }})</el-radio-button>
            </el-radio-group>
            <el-button type="success" size="small" @click="exportSsl" :disabled="!sslCounts.total">
              📥 导出
            </el-button>
          </div>
        </div>
      </template>
      <el-table :data="sslFiltered" stripe size="small" style="width: 100%" max-height="400"
        :row-class-name="sslRowClass">
        <el-table-column prop="name" label="站点" min-width="200">
          <template #default="{ row }">
            <router-link :to="`/targets/${row.target_id}`" style="color: #409eff; text-decoration: none">
              {{ row.name || row.url }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="group" label="分组" width="120" />
        <el-table-column label="证书状态" width="120" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ssl_status === 'valid'" type="success" size="small">正常</el-tag>
            <el-tag v-else-if="row.ssl_status === 'invalid'" type="danger" size="small">证书异常</el-tag>
            <el-tag v-else-if="row.ssl_status === 'expiring'" type="warning" size="small">即将过期</el-tag>
            <el-tag v-else-if="row.ssl_status === 'no_https'" size="small">HTTP未加密</el-tag>
            <el-tag v-else type="info" size="small">未检测</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="剩余天数" width="110" align="center" sortable :sort-method="(a, b) => (a.ssl_days_left ?? 9999) - (b.ssl_days_left ?? 9999)">
          <template #default="{ row }">
            <span v-if="row.ssl_days_left != null"
              :style="{
                color: row.ssl_days_left <= 0 ? '#f56c6c' : row.ssl_days_left <= 7 ? '#f56c6c' : row.ssl_days_left <= 30 ? '#e6a23c' : '#67c23a',
                fontWeight: row.ssl_days_left <= 30 ? 700 : 400,
              }">
              {{ row.ssl_days_left <= 0 ? '已过期 ' + Math.abs(row.ssl_days_left) + '天' : row.ssl_days_left + ' 天' }}
            </span>
            <span v-else style="color: #ccc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="ssl_issuer" label="签发者" min-width="180" show-overflow-tooltip />
        <el-table-column prop="ssl_subject" label="证书主体" min-width="160" show-overflow-tooltip />
        <el-table-column label="到期时间" width="160">
          <template #default="{ row }">{{ formatTime(row.ssl_not_after) }}</template>
        </el-table-column>
        <el-table-column prop="ssl_error" label="证书错误" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.ssl_error" style="color: #f56c6c">{{ row.ssl_error }}</span>
            <span v-else-if="row.ssl_warning" style="color: #e6a23c">{{ row.ssl_warning }}</span>
            <span v-else style="color: #ccc">-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- Recent Failures -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
          <span style="font-weight: 600">🔴 失败站点
            <el-tag size="small" type="danger" v-if="failures.length">{{ failures.length }}</el-tag>
          </span>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <!-- Error type multi-select filter -->
            <el-select v-model="selectedErrorTypes" multiple collapse-tags collapse-tags-tooltip
              placeholder="错误类型" clearable size="small" style="width: 260px" @change="loadFailures">
              <el-option v-for="et in errorTypeOptions" :key="et.value" :label="et.label" :value="et.value">
                <span>{{ et.icon }} {{ et.label }}</span>
                <span v-if="errorTypeCounts[et.value]" style="float: right; color: #999; font-size: 12px">
                  {{ errorTypeCounts[et.value] }}
                </span>
              </el-option>
            </el-select>
            <!-- Group filter -->
            <el-select v-model="failureGroup" placeholder="全部分组" clearable size="small" style="width: 140px" @change="loadFailures">
              <el-option v-for="g in groupStats" :key="g.group" :label="g.group" :value="g.group" />
            </el-select>
            <!-- Retry (excludes domain expired) -->
            <el-button type="warning" size="small"
              :loading="retrying" @click="retryFailed"
              :disabled="retrying || !retryableCount">
              {{ retrying ? `重试中 ${retryInfo.done}/${retryInfo.total}` : `🔄 重试 (${retryableCount})` }}
            </el-button>
            <!-- Export -->
            <el-button type="success" size="small" @click="exportFailures" :disabled="!failures.length">
              📥 导出
            </el-button>
          </div>
        </div>
      </template>
      <!-- Error type summary badges -->
      <div v-if="allFailures.length && !selectedErrorTypes.length" style="margin-bottom: 12px; display: flex; gap: 6px; flex-wrap: wrap">
        <el-tag v-for="et in errorTypeOptions" :key="et.value"
          :type="et.tagType" size="small" style="cursor: pointer"
          :effect="selectedErrorTypes.includes(et.value) ? 'dark' : 'plain'"
          @click="toggleErrorType(et.value)"
          v-show="errorTypeCounts[et.value] > 0">
          {{ et.icon }} {{ et.label }}: {{ errorTypeCounts[et.value] || 0 }}
        </el-tag>
      </div>
      <el-table :data="failures" stripe size="small" style="width: 100%" empty-text="暂无异常，一切正常 ✅"
        max-height="600">
        <el-table-column prop="name" label="站点" min-width="200">
          <template #default="{ row }">
            <router-link :to="`/targets/${row.target_id}`" style="color: #409eff; text-decoration: none">
              {{ row.name || row.url }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="group" label="分组" width="120" />
        <el-table-column prop="error_type" label="类型" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="errorTypeTagType(row.error_type)" size="small" effect="plain">
              {{ errorTypeLabel(row.error_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status_code" label="状态码" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status_code >= 400 ? 'danger' : 'info'" size="small" v-if="row.status_code">
              {{ row.status_code }}
            </el-tag>
            <span v-else style="color: #ccc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误详情" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span>{{ row.error }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="consecutive_fails" label="连续失败" width="90" align="center">
          <template #default="{ row }">
            <el-tag type="danger" size="small">{{ row.consecutive_fails }}次</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="has_anomaly" label="渲染异常" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.has_anomaly" type="warning" size="small">异常</el-tag>
            <span v-else style="color: #ccc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_check_at" label="检测时间" width="160">
          <template #default="{ row }">{{ formatTime(row.last_check_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import dayjs from 'dayjs'

const stats = ref({})
const groupStats = ref([])
const allFailures = ref([])
const failures = ref([])
const failureGroup = ref('')
const selectedErrorTypes = ref([])
const retrying = ref(false)
const refreshing = ref(false)
const retryInfo = ref({ total: 0, done: 0, ok: 0, fail: 0, skipped_expired: 0 })
let retryTimer = null

// ---- Auto Refresh ----
const autoRefreshInterval = ref(0)
let autoRefreshTimer = null

function setupAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
  if (autoRefreshInterval.value > 0) {
    autoRefreshTimer = setInterval(() => {
      if (!retrying.value) {
        refreshAll()
      }
    }, autoRefreshInterval.value * 1000)
  }
}

async function manualRefresh() {
  refreshing.value = true
  try {
    await refreshAll()
    ElMessage.success('已刷新')
  } catch (e) {
    ElMessage.error('刷新失败')
  } finally {
    refreshing.value = false
  }
}

// ---- SSL data ----
const sslSummaryData = ref({ total: 0, valid: 0, invalid: 0, expiring: 0, noHttps: 0, unchecked: 0 })
const sslFiltered = ref([])   // preview list (top 200)
const sslFilter = ref('all')

const sslCounts = computed(() => sslSummaryData.value)

function sslRowClass({ row }) {
  if (row.ssl_status === 'invalid') return 'ssl-row-error'
  if (row.ssl_status === 'expiring') return 'ssl-row-warning'
  return ''
}

async function loadSslSummary() {
  try {
    const { data } = await api.getSslSummary()
    sslSummaryData.value = data
  } catch (e) {}
}

async function checkGroup(group) {
  try {
    await api.triggerCheckAll(group)
    ElMessage.success(`已触发分组 [${group}] 检测`)
  } catch (e) {
    ElMessage.error('触发失败')
  }
}

async function loadSslData() {
  const filterVal = sslFilter.value === 'all' ? undefined : sslFilter.value
  try {
    const { data } = await api.getSslOverview(undefined, filterVal, 200)
    sslFiltered.value = data
  } catch (e) {}
}

function exportSsl() {
  const filterVal = sslFilter.value === 'all' ? undefined : sslFilter.value
  const url = api.exportSsl(undefined, filterVal)
  window.open(url, '_blank')
}

// Error type option definitions
const errorTypeOptions = [
  { value: 'domain_expired', label: '域名过期/停放', icon: '🌐', tagType: 'warning' },
  { value: 'dns_not_found',  label: 'DNS无法解析', icon: '🚫', tagType: 'info' },
  { value: 'timeout',        label: '超时',         icon: '⏱️', tagType: 'info' },
  { value: 'connect_error',  label: '连接失败',     icon: '🔌', tagType: 'danger' },
  { value: 'ssl_error',      label: 'SSL错误',      icon: '🔒', tagType: 'danger' },
  { value: 'ssl_cert_error', label: '证书异常',     icon: '📜', tagType: 'warning' },
  { value: 'status_mismatch',label: '状态码不匹配', icon: '📊', tagType: 'danger' },
  { value: 'keyword_missing',label: '关键词缺失',   icon: '🔍', tagType: 'warning' },
  { value: 'other',          label: '其他',         icon: '❓', tagType: 'info' },
]

const errorTypeCounts = computed(() => {
  const counts = {}
  for (const f of allFailures.value) {
    const t = f.error_type || 'other'
    counts[t] = (counts[t] || 0) + 1
  }
  return counts
})

const retryableCount = computed(() => {
  return allFailures.value.filter(f => f.error_type !== 'domain_expired' && f.error_type !== 'dns_not_found').length
})

const cards = computed(() => [
  { label: '监控总数', value: stats.value.total_targets || 0, color: '#333' },
  { label: '已启用', value: stats.value.enabled_targets || 0, color: '#409eff' },
  { label: '健康', value: stats.value.healthy || 0, color: '#67c23a' },
  { label: '异常', value: stats.value.unhealthy || 0, color: '#f56c6c' },
  { label: '待检测', value: stats.value.unknown || 0, color: '#909399' },
  { label: '今日截图', value: stats.value.screenshots_today || 0, color: '#e6a23c' },
])

function formatTime(t) {
  return t ? dayjs(t).format('MM-DD HH:mm:ss') : '-'
}

function pct(val, total) {
  if (!total || total === 0) return 0
  return Math.max(5, Math.round(val / total * 100))
}

function onGroupSelect(row) {
  if (row) {
    failureGroup.value = row.group
    loadFailures()
  }
}

function errorTypeLabel(type) {
  const opt = errorTypeOptions.find(o => o.value === type)
  return opt ? opt.label : type || '未知'
}

function errorTypeTagType(type) {
  const opt = errorTypeOptions.find(o => o.value === type)
  return opt ? opt.tagType : 'info'
}

function toggleErrorType(type) {
  const idx = selectedErrorTypes.value.indexOf(type)
  if (idx >= 0) {
    selectedErrorTypes.value.splice(idx, 1)
  } else {
    selectedErrorTypes.value.push(type)
  }
  loadFailures()
}

async function loadFailures() {
  const group = failureGroup.value || undefined
  const types = selectedErrorTypes.value.length ? selectedErrorTypes.value : undefined

  if (types) {
    const [filtered, unfiltered] = await Promise.all([
      api.getRecentFailures(group, types),
      api.getRecentFailures(group),
    ])
    failures.value = filtered.data
    allFailures.value = unfiltered.data
  } else {
    const { data } = await api.getRecentFailures(group)
    failures.value = data
    allFailures.value = data
  }
}

function exportFailures() {
  const url = api.exportFailures(
    failureGroup.value || undefined,
    selectedErrorTypes.value.length ? selectedErrorTypes.value : undefined,
  )
  window.open(url, '_blank')
}

async function retryFailed() {
  retrying.value = true
  retryInfo.value = { total: 0, done: 0, ok: 0, fail: 0, skipped_expired: 0 }
  try {
    await api.triggerRetryFailed(failureGroup.value || undefined)
    retryTimer = setInterval(async () => {
      try {
        const { data } = await api.getRetryProgress()
        retryInfo.value = data
        if (!data.running) {
          clearInterval(retryTimer)
          retryTimer = null
          retrying.value = false
          await refreshAll()
          const parts = [`${data.ok} 恢复`, `${data.fail} 仍失败`]
          if (data.skipped_expired) parts.push(`${data.skipped_expired} 域名过期已跳过`)
          ElMessage.success(`重试完成: ${parts.join(', ')}`)
        }
      } catch { /* ignore */ }
    }, 1000)
  } catch {
    retrying.value = false
    ElMessage.error('触发重试失败')
  }
}

async function refreshAll() {
  const group = failureGroup.value || undefined
  const types = selectedErrorTypes.value.length ? selectedErrorTypes.value : undefined
  const [s, g] = await Promise.all([
    api.getStats(),
    api.getGroupStats(),
  ])
  stats.value = s.data
  groupStats.value = g.data
  await loadSslData()
  if (types) {
    const [filtered, unfiltered] = await Promise.all([
      api.getRecentFailures(group, types),
      api.getRecentFailures(group),
    ])
    failures.value = filtered.data
    allFailures.value = unfiltered.data
  } else {
    const { data } = await api.getRecentFailures(group)
    failures.value = data
    allFailures.value = data
  }
}

function groupSummary({ columns, data }) {
  const sums = []
  columns.forEach((col, index) => {
    if (index === 0) { sums[index] = '合计'; return }
    const prop = col.property
    if (['total', 'enabled', 'healthy', 'unhealthy', 'unknown', 'render_anomaly', 'ssl_checked', 'ssl_ok', 'ssl_error', 'ssl_expiring_soon'].includes(prop)) {
      sums[index] = data.reduce((sum, row) => sum + (row[prop] || 0), 0)
    } else if (col.label === '可用率') {
      const te = data.reduce((s, r) => s + (r.enabled || 0), 0)
      const th = data.reduce((s, r) => s + (r.healthy || 0), 0)
      sums[index] = te > 0 ? (th / te * 100).toFixed(1) + '%' : '-'
    } else if (col.label === '🔒 证书') {
      const ok = data.reduce((s, r) => s + (r.ssl_ok || 0), 0)
      const err = data.reduce((s, r) => s + (r.ssl_error || 0), 0)
      const exp = data.reduce((s, r) => s + (r.ssl_expiring_soon || 0), 0)
      const parts = [`${ok}正常`]
      if (err > 0) parts.push(`${err}异常`)
      if (exp > 0) parts.push(`${exp}将过期`)
      sums[index] = parts.join('/')
    } else {
      sums[index] = ''
    }
  })
  return sums
}

onMounted(async () => {
  const [s, g, f, summary, ssl] = await Promise.all([
    api.getStats(),
    api.getGroupStats(),
    api.getRecentFailures(),
    api.getSslSummary(),
    api.getSslOverview(undefined, undefined, 200),
  ])
  stats.value = s.data
  groupStats.value = g.data
  failures.value = f.data
  allFailures.value = f.data
  sslSummaryData.value = summary.data
  sslFiltered.value = ssl.data
})

onUnmounted(() => {
  if (retryTimer) {
    clearInterval(retryTimer)
    retryTimer = null
  }
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
})
</script>

<style scoped>
:deep(.ssl-row-error) {
  background-color: #fef0f0 !important;
}
:deep(.ssl-row-warning) {
  background-color: #fdf6ec !important;
}
</style>
