<template>
  <div v-loading="loading">
    <!-- Header -->
    <el-page-header @back="$router.push('/targets')" style="margin-bottom: 20px">
      <template #content>
        <span style="font-size: 18px; font-weight: 600">{{ target?.name || target?.url }}</span>
        <el-tag v-if="target?.status?.is_ok === true" type="success" size="small" style="margin-left: 12px">正常</el-tag>
        <el-tag v-else-if="target?.status?.is_ok === false" type="danger" size="small" style="margin-left: 12px">异常</el-tag>
        <el-tag v-else type="info" size="small" style="margin-left: 12px">未检测</el-tag>
      </template>
    </el-page-header>

    <!-- Info + Stats Cards -->
    <el-row :gutter="16" style="margin-bottom: 20px">
      <el-col :span="8">
        <el-card>
          <template #header><span style="font-weight: 600">基本信息</span></template>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="URL">
              <a :href="target?.url" target="_blank" style="color: #409eff">{{ target?.url }}</a>
            </el-descriptions-item>
            <el-descriptions-item label="分组">{{ target?.group }}</el-descriptions-item>
            <el-descriptions-item label="检测间隔">{{ target?.check_interval }}s</el-descriptions-item>
            <el-descriptions-item label="截图间隔">{{ target?.shot_interval }}s</el-descriptions-item>
            <el-descriptions-item label="期望状态码">{{ target?.expect_status }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header><span style="font-weight: 600">24小时统计</span></template>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="总检测">{{ stats24?.total_checks || 0 }} 次</el-descriptions-item>
            <el-descriptions-item label="成功">{{ stats24?.ok_checks || 0 }} 次</el-descriptions-item>
            <el-descriptions-item label="可用率">
              <span :style="{ color: (stats24?.uptime_pct || 0) < 99 ? '#f56c6c' : '#67c23a', fontWeight: 700 }">
                {{ stats24?.uptime_pct ?? '-' }}%
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="平均延迟">{{ stats24?.avg_latency_ms ?? '-' }} ms</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>
            <span style="font-weight: 600">最新状态</span>
          </template>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="状态码">{{ target?.status?.last_status_code || '-' }}</el-descriptions-item>
            <el-descriptions-item label="延迟">{{ target?.status?.last_latency_ms || '-' }} ms</el-descriptions-item>
            <el-descriptions-item label="连续失败">{{ target?.status?.consecutive_fails || 0 }}</el-descriptions-item>
            <el-descriptions-item label="错误">{{ target?.status?.last_error || '无' }}</el-descriptions-item>
            <el-descriptions-item label="检测时间">{{ formatTime(target?.status?.last_check_at) }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- Tabs: Check History / Screenshots -->
    <el-card>
      <el-tabs v-model="activeTab">
        <!-- Check History -->
        <el-tab-pane label="检测历史" name="checks">
          <el-table :data="checkResults" stripe size="small">
            <el-table-column prop="checked_at" label="时间" width="170">
              <template #default="{ row }">{{ formatTime(row.checked_at) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.is_ok ? 'success' : 'danger'" size="small">{{ row.is_ok ? 'OK' : 'FAIL' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status_code" label="状态码" width="80" align="center" />
            <el-table-column prop="latency_ms" label="延迟(ms)" width="100" align="center" />
            <el-table-column prop="error" label="错误" show-overflow-tooltip />
          </el-table>
          <el-pagination
            v-model:current-page="checkPage" :total="checkTotal" :page-size="30"
            layout="total, prev, pager, next" style="margin-top: 12px"
            @current-change="loadChecks"
          />
        </el-tab-pane>

        <!-- Screenshots -->
        <el-tab-pane label="截图历史" name="screenshots">
          <div style="display: flex; flex-wrap: wrap; gap: 16px">
            <el-card v-for="s in screenshots" :key="s.id" shadow="hover"
              style="width: 340px; cursor: pointer" @click="previewShot = s">
              <img :src="getThumbUrl(s)" style="width: 100%; height: 200px; object-fit: cover; border-radius: 4px" />
              <div style="margin-top: 8px; font-size: 12px; color: #666">
                {{ formatTime(s.taken_at) }}
                <el-tag v-if="s.is_anomaly" type="danger" size="small" style="margin-left: 8px">
                  异常 {{ s.anomaly_score }}
                </el-tag>
              </div>
              <div style="font-size: 12px; color: #999" v-if="s.page_title">{{ s.page_title }}</div>
            </el-card>
          </div>
          <el-pagination
            v-model:current-page="shotPage" :total="shotTotal" :page-size="12"
            layout="total, prev, pager, next" style="margin-top: 12px"
            @current-change="loadScreenshots"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- Screenshot Preview Dialog -->
    <el-dialog v-model="showPreview" width="80%" :title="previewShot?.page_title || '截图预览'">
      <div v-if="previewShot" style="text-align: center">
        <img :src="getFullUrl(previewShot)" style="max-width: 100%; border: 1px solid #eee; border-radius: 4px" />
        <el-descriptions :column="3" size="small" border style="margin-top: 12px">
          <el-descriptions-item label="尺寸">{{ previewShot.width }}×{{ previewShot.height }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{ ((previewShot.file_size || 0) / 1024).toFixed(1) }}KB</el-descriptions-item>
          <el-descriptions-item label="DOM文本长度">{{ previewShot.dom_text_length }}</el-descriptions-item>
          <el-descriptions-item label="控制台错误">{{ previewShot.console_errors }}</el-descriptions-item>
          <el-descriptions-item label="失败请求">{{ previewShot.failed_requests }}</el-descriptions-item>
          <el-descriptions-item label="异常评分">
            <el-tag :type="previewShot.anomaly_score >= 30 ? 'danger' : 'success'" size="small">
              {{ previewShot.anomaly_score }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <div v-if="previewShot.anomaly_reasons?.length" style="margin-top: 12px; text-align: left">
          <el-tag v-for="(r, i) in previewShot.anomaly_reasons" :key="i" type="warning" style="margin: 4px" size="small">
            {{ r.rule }}: {{ r.detail }}
          </el-tag>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api/index.js'
import dayjs from 'dayjs'

const route = useRoute()
const targetId = computed(() => parseInt(route.params.id))

const loading = ref(true)
const target = ref(null)
const stats24 = ref(null)
const activeTab = ref('checks')

// Checks
const checkResults = ref([])
const checkPage = ref(1)
const checkTotal = ref(0)

// Screenshots
const screenshots = ref([])
const shotPage = ref(1)
const shotTotal = ref(0)

// Preview
const previewShot = ref(null)
const showPreview = computed({
  get: () => previewShot.value !== null,
  set: (v) => { if (!v) previewShot.value = null },
})

function formatTime(t) { return t ? dayjs(t).format('MM-DD HH:mm:ss') : '-' }
function getThumbUrl(s) { return api.getScreenshotUrl(s.id, true) }
function getFullUrl(s) { return api.getScreenshotUrl(s.id, false) }

async function loadTarget() {
  const { data } = await api.getTarget(targetId.value)
  target.value = data
}

async function loadStats() {
  const { data } = await api.getStats24h(targetId.value)
  stats24.value = data
}

async function loadChecks() {
  const { data } = await api.getResults(targetId.value, { page: checkPage.value, size: 30 })
  checkResults.value = data.items
  checkTotal.value = data.total
}

async function loadScreenshots() {
  const { data } = await api.getScreenshots(targetId.value, { page: shotPage.value, size: 12 })
  screenshots.value = data.items
  shotTotal.value = data.total
}

onMounted(async () => {
  await Promise.all([loadTarget(), loadStats(), loadChecks(), loadScreenshots()])
  loading.value = false
})

watch(activeTab, (tab) => {
  if (tab === 'checks') loadChecks()
  else loadScreenshots()
})
</script>
