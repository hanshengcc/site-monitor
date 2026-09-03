<template>
  <div>
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
      </el-table>
    </el-card>

    <!-- Recent Failures -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">🔴 最近失败的站点</span>
          <div style="display: flex; align-items: center; gap: 12px">
            <el-select v-model="failureGroup" placeholder="全部分组" clearable size="small" style="width: 160px" @change="loadFailures">
              <el-option v-for="g in groupStats" :key="g.group" :label="g.group" :value="g.group" />
            </el-select>
            <el-tag v-if="failureGroup" closable size="small" @close="failureGroup = ''; loadFailures()">
              {{ failureGroup }}
            </el-tag>
          </div>
        </div>
      </template>
      <el-table :data="failures" stripe size="small" style="width: 100%" empty-text="暂无异常，一切正常 ✅">
        <el-table-column prop="name" label="站点" min-width="200">
          <template #default="{ row }">
            <router-link :to="`/targets/${row.target_id}`" style="color: #409eff; text-decoration: none">
              {{ row.name || row.url }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="group" label="分组" width="120" />
        <el-table-column prop="status_code" label="状态码" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status_code >= 400 ? 'danger' : 'info'" size="small">
              {{ row.status_code || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="200" show-overflow-tooltip />
        <el-table-column prop="consecutive_fails" label="连续失败" width="90" align="center">
          <template #default="{ row }">
            <el-tag type="danger" size="small">{{ row.consecutive_fails }}次</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="has_anomaly" label="渲染异常" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.has_anomaly" type="warning" size="small">异常</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_check_at" label="检测时间" width="170">
          <template #default="{ row }">{{ formatTime(row.last_check_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api/index.js'
import dayjs from 'dayjs'

const stats = ref({})
const groupStats = ref([])
const failures = ref([])
const failureGroup = ref('')

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

async function loadFailures() {
  const { data } = await api.getRecentFailures(50, failureGroup.value || undefined)
  failures.value = data
}

function groupSummary({ columns, data }) {
  const sums = []
  columns.forEach((col, index) => {
    if (index === 0) { sums[index] = '合计'; return }
    const prop = col.property
    if (['total', 'enabled', 'healthy', 'unhealthy', 'unknown', 'render_anomaly'].includes(prop)) {
      sums[index] = data.reduce((sum, row) => sum + (row[prop] || 0), 0)
    } else if (col.label === '可用率') {
      const te = data.reduce((s, r) => s + (r.enabled || 0), 0)
      const th = data.reduce((s, r) => s + (r.healthy || 0), 0)
      sums[index] = te > 0 ? (th / te * 100).toFixed(1) + '%' : '-'
    } else {
      sums[index] = ''
    }
  })
  return sums
}

onMounted(async () => {
  const [s, g, f] = await Promise.all([
    api.getStats(),
    api.getGroupStats(),
    api.getRecentFailures(),
  ])
  stats.value = s.data
  groupStats.value = g.data
  failures.value = f.data
})
</script>
