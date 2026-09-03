<template>
  <div>
    <!-- Filters -->
    <el-row :gutter="12" style="margin-bottom: 16px">
      <el-col :span="4">
        <el-select v-model="filterState" placeholder="状态" clearable @change="loadData">
          <el-option label="未处理" value="open" />
          <el-option label="已确认" value="acked" />
          <el-option label="已解决" value="resolved" />
          <el-option label="误报" value="false_positive" />
        </el-select>
      </el-col>
    </el-row>

    <el-card>
      <el-table :data="anomalies" stripe size="small" v-loading="loading">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="target_id" label="目标ID" width="80">
          <template #default="{ row }">
            <router-link :to="`/targets/${row.target_id}`" style="color: #409eff">{{ row.target_id }}</router-link>
          </template>
        </el-table-column>
        <el-table-column prop="anomaly_type" label="类型" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="typeColor(row.anomaly_type)">{{ row.anomaly_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="score" label="评分" width="70" align="center">
          <template #default="{ row }">
            <span :style="{ color: row.score >= 50 ? '#f56c6c' : '#e6a23c', fontWeight: 700 }">{{ row.score }}</span>
          </template>
        </el-table-column>
        <el-table-column label="原因" min-width="300">
          <template #default="{ row }">
            <el-tag v-for="(r, i) in (row.reasons || [])" :key="i" size="small" type="info" style="margin: 2px">
              {{ r.rule }}: {{ r.detail }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="截图" width="80" align="center">
          <template #default="{ row }">
            <el-button v-if="row.screenshot_id" link size="small" @click="viewShot(row.screenshot_id)">
              <el-icon><Picture /></el-icon>
            </el-button>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="state" label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="stateColor(row.state)" size="small">{{ stateLabel(row.state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="detected_at" label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.detected_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="center">
          <template #default="{ row }">
            <el-button v-if="row.state === 'open'" link size="small" @click="updateState(row, 'acked')">确认</el-button>
            <el-button v-if="row.state !== 'resolved'" link size="small" type="success" @click="updateState(row, 'resolved')">解决</el-button>
            <el-button v-if="row.state !== 'false_positive'" link size="small" type="warning" @click="updateState(row, 'false_positive')">误报</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page" :total="total" :page-size="50"
        layout="total, prev, pager, next" style="margin-top: 12px"
        @current-change="loadData"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import dayjs from 'dayjs'

const anomalies = ref([])
const loading = ref(false)
const page = ref(1)
const total = ref(0)
const filterState = ref('open')

function formatTime(t) { return t ? dayjs(t).format('MM-DD HH:mm:ss') : '-' }
function typeColor(t) {
  return { http_error: 'danger', keyword_error: 'warning', white_screen: 'danger', render_diff: 'warning' }[t] || 'info'
}
function stateColor(s) {
  return { open: 'danger', acked: 'warning', resolved: 'success', false_positive: 'info' }[s] || 'info'
}
function stateLabel(s) {
  return { open: '未处理', acked: '已确认', resolved: '已解决', false_positive: '误报' }[s] || s
}

async function loadData() {
  loading.value = true
  const { data } = await api.getAnomalies({ page: page.value, state: filterState.value || undefined })
  anomalies.value = data.items
  total.value = data.total
  loading.value = false
}

async function updateState(row, state) {
  await api.updateAnomaly(row.id, { state })
  ElMessage.success('已更新')
  loadData()
}

function viewShot(id) {
  window.open(api.getScreenshotUrl(id, false), '_blank')
}

onMounted(loadData)
</script>
