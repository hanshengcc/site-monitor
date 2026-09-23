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
            <el-descriptions-item label="快照间隔">{{ target?.snapshot_interval || 21600 }}s</el-descriptions-item>
            <el-descriptions-item label="期望状态码">{{ target?.expect_status }}</el-descriptions-item>
            <el-descriptions-item label="期望DNS">{{ target?.expect_dns_server || '-' }}</el-descriptions-item>
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
            <el-descriptions-item label="DNS服务器">
              <span v-if="target?.status?.dns_server" style="font-family: monospace; color: #409eff">
                {{ target.status.dns_server }}
              </span>
              <span v-else>-</span>
            </el-descriptions-item>
            <el-descriptions-item label="状态码">{{ target?.status?.last_status_code || '-' }}</el-descriptions-item>
            <el-descriptions-item label="延迟">{{ target?.status?.last_latency_ms || '-' }} ms</el-descriptions-item>
            <el-descriptions-item label="连续失败">{{ target?.status?.consecutive_fails || 0 }}</el-descriptions-item>
            <el-descriptions-item label="错误">{{ target?.status?.last_error || '无' }}</el-descriptions-item>
            <el-descriptions-item label="最新快照" v-if="target?.status?.last_snapshot_at">
              {{ formatTime(target?.status?.last_snapshot_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="检测时间">{{ formatTime(target?.status?.last_check_at) }}</el-descriptions-item>
            <el-descriptions-item label="🔒 证书状态" v-if="target?.status?.ssl_checked_at">
              <el-tag :type="target?.status?.ssl_valid ? 'success' : 'danger'" size="small">
                {{ target?.status?.ssl_valid ? '正常' : '异常' }}
              </el-tag>
              <span v-if="target?.status?.ssl_days_left != null" style="margin-left: 8px; font-size: 12px"
                :style="{ color: target?.status?.ssl_days_left <= 7 ? '#f56c6c' : target?.status?.ssl_days_left <= 30 ? '#e6a23c' : '#67c23a' }">
                剩余 {{ target?.status?.ssl_days_left }} 天
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="证书签发者" v-if="target?.status?.ssl_issuer">
              {{ target?.status?.ssl_issuer }}
            </el-descriptions-item>
            <el-descriptions-item label="证书过期" v-if="target?.status?.ssl_not_after">
              {{ formatTime(target?.status?.ssl_not_after) }}
            </el-descriptions-item>
            <el-descriptions-item label="证书错误" v-if="target?.status?.ssl_error">
              <span style="color: #f56c6c">{{ target?.status?.ssl_error }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="证书警告" v-if="target?.status?.ssl_warning">
              <span style="color: #e6a23c">{{ target?.status?.ssl_warning }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- Tabs: Check History / Screenshots / Snapshots (网页时光机) -->
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
          <div style="display: flex; justify-content: flex-end; margin-bottom: 12px">
            <el-button type="primary" size="small" :loading="capturingShot" @click="doCaptureShot">
              <el-icon><Camera /></el-icon> 立即截图
            </el-button>
          </div>
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
          <div v-if="!screenshots.length" style="text-align: center; color: #999; padding: 40px">
            暂无截图记录
          </div>
          <el-pagination
            v-model:current-page="shotPage" :total="shotTotal" :page-size="12"
            layout="total, prev, pager, next" style="margin-top: 12px"
            @current-change="loadScreenshots"
          />
        </el-tab-pane>

        <!-- Snapshots (网页时光机) -->
        <el-tab-pane label="🕰️ 网页快照 (时光机)" name="snapshots">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 10px;">
            <div style="display: flex; align-items: center; gap: 12px;">
              <span style="font-size: 14px; font-weight: 600; color: #303133">
                网页时光机历史存档
              </span>
              <el-tag size="small" type="info">共 {{ snapTotal }} 个历史快照</el-tag>
              <el-checkbox v-model="snapChangedOnly" @change="loadSnapshots">
                仅看内容变更版本
              </el-checkbox>
            </div>
            <div>
              <el-button type="primary" size="small" :loading="capturingSnapshot" @click="doCaptureSnapshot">
                <el-icon><Clock /></el-icon> 立即抓取网页快照
              </el-button>
            </div>
          </div>

          <el-table :data="snapshots" stripe size="small" style="width: 100%" v-loading="snapLoading">
            <el-table-column prop="taken_at" label="快照时间" width="170">
              <template #default="{ row }">
                <span style="font-weight: 500">{{ formatTime(row.taken_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="page_title" label="页面标题" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <span>{{ row.page_title || '(无标题)' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="http_status" label="状态码" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.http_status === 200 ? 'success' : 'danger'" size="small">
                  {{ row.http_status || '-' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="HTML 大小" width="130" align="center">
              <template #default="{ row }">
                <span>{{ formatBytes(row.file_size) }}</span>
                <span style="color: #999; font-size: 11px; margin-left: 4px" v-if="row.compressed_size">
                  (gz {{ formatBytes(row.compressed_size) }})
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="dom_text_length" label="文本长度" width="90" align="center" />
            <el-table-column label="变更状态" width="110" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.has_changed" type="warning" size="small" effect="plain">
                  ⚡ 内容变更
                </el-tag>
                <el-tag v-else type="info" size="small" effect="plain">
                  与上版一致
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="240" align="center" fixed="right">
              <template #default="{ row }">
                <el-button link size="small" type="primary" @click="openWaybackPreview(row)">
                  🕰️ 时光机
                </el-button>
                <el-button link size="small" @click="viewSource(row)">
                  源码
                </el-button>
                <el-button link size="small" @click="showDiff(row)">
                  对比
                </el-button>
                <el-button link size="small" @click="downloadSnap(row)">
                  下载
                </el-button>
                <el-popconfirm
                  title="确认删除该历史快照？"
                  confirm-button-text="删除"
                  cancel-button-text="取消"
                  confirm-button-type="danger"
                  @confirm="deleteSnap(row)"
                >
                  <template #reference>
                    <el-button link size="small" type="danger">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="!snapshots.length && !snapLoading" style="text-align: center; color: #999; padding: 40px">
            暂无网页快照记录，点击上方「立即抓取网页快照」进行第一次时光机归档
          </div>

          <el-pagination
            v-model:current-page="snapPage" :total="snapTotal" :page-size="snapSize"
            layout="total, prev, pager, next" style="margin-top: 12px; justify-content: flex-end"
            @current-change="loadSnapshots"
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

    <!-- Wayback Machine Snapshot Preview Dialog -->
    <el-dialog v-model="showWaybackDialog" width="90%" top="4vh" :title="`🕰️ 网页时光机快照回溯 [${formatTime(currentSnap?.taken_at)}]`" destroy-on-close>
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
        <span style="font-size: 13px; color: #606266">
          {{ currentSnap?.page_title }} ({{ target?.url }})
        </span>
        <div>
          <el-button size="small" type="primary" plain @click="openSnapInNewWindow(currentSnap?.id)">
            在新标签页全屏打开 ↗
          </el-button>
        </div>
      </div>
      <div style="height: 75vh; border: 1px solid #dcdfe6; border-radius: 4px; overflow: hidden;">
        <iframe
          v-if="currentSnap"
          :src="api.getSnapshotViewUrl(currentSnap.id)"
          style="width: 100%; height: 100%; border: none;"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
        />
      </div>
    </el-dialog>

    <!-- Raw HTML Source Dialog -->
    <el-dialog v-model="showSourceDialog" width="80%" title="📜 快照原始 HTML 源码" destroy-on-close>
      <div style="display: flex; justify-content: flex-end; margin-bottom: 8px;">
        <el-button size="small" type="primary" @click="copyRawHtml">复制源码</el-button>
      </div>
      <el-input
        v-model="rawSourceCode"
        type="textarea"
        :rows="22"
        readonly
        style="font-family: monospace; font-size: 12px"
      />
    </el-dialog>

    <!-- Snapshot Diff Dialog -->
    <el-dialog v-model="showDiffDialog" width="600px" title="⚖️ 快照版本变更对比">
      <div v-if="diffData">
        <el-alert
          :type="diffData.has_changed ? 'warning' : 'success'"
          :title="diffData.has_changed ? '⚡ 该版本相比上一快照检测到页面内容有变化' : '⏺ 该版本相比上一快照内容完全一致'"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="当前快照时间">{{ formatTime(diffData.current?.taken_at) }}</el-descriptions-item>
          <el-descriptions-item label="上个快照时间">{{ formatTime(diffData.previous?.taken_at) || '无上一版本' }}</el-descriptions-item>
          <el-descriptions-item label="当前页面标题">{{ diffData.current?.page_title || '-' }}</el-descriptions-item>
          <el-descriptions-item label="上个页面标题">{{ diffData.previous?.page_title || '-' }}</el-descriptions-item>
          <el-descriptions-item label="当前状态码">{{ diffData.current?.http_status }}</el-descriptions-item>
          <el-descriptions-item label="上个状态码">{{ diffData.previous?.http_status || '-' }}</el-descriptions-item>
          <el-descriptions-item label="大小差异">
            <span :style="{ color: diffData.size_diff > 0 ? '#f56c6c' : diffData.size_diff < 0 ? '#409eff' : '#67c23a' }">
              {{ diffData.size_diff > 0 ? `+${diffData.size_diff} 字节` : `${diffData.size_diff} 字节` }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="SHA256 哈希">{{ diffData.current?.content_hash?.substring(0, 16) }}...</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
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
const capturingShot = ref(false)

// Snapshots (网页时光机)
const snapshots = ref([])
const snapPage = ref(1)
const snapSize = ref(20)
const snapTotal = ref(0)
const snapLoading = ref(false)
const snapChangedOnly = ref(false)
const capturingSnapshot = ref(false)

// Dialogs state
const previewShot = ref(null)
const showPreview = computed({
  get: () => previewShot.value !== null,
  set: (v) => { if (!v) previewShot.value = null },
})

const showWaybackDialog = ref(false)
const currentSnap = ref(null)

const showSourceDialog = ref(false)
const rawSourceCode = ref('')

const showDiffDialog = ref(false)
const diffData = ref(null)

function formatTime(t) { return t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-' }
function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}
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

async function loadSnapshots() {
  snapLoading.value = true
  try {
    const { data } = await api.getSnapshots(targetId.value, {
      page: snapPage.value,
      size: snapSize.value,
      changed_only: snapChangedOnly.value,
    })
    snapshots.value = data.items
    snapTotal.value = data.total
  } catch (e) {
    ElMessage.error('加载网页快照失败')
  } finally {
    snapLoading.value = false
  }
}

async function doCaptureShot() {
  capturingShot.value = true
  try {
    ElMessage.info('截图中，请稍候...')
    await api.triggerScreenshot(targetId.value)
    ElMessage.success('截图完成')
    await loadScreenshots()
  } catch (e) {
    ElMessage.error('截图失败')
  } finally {
    capturingShot.value = false
  }
}

async function doCaptureSnapshot() {
  capturingSnapshot.value = true
  try {
    ElMessage.info('正在抓取网页时光机快照...')
    const { data } = await api.triggerSnapshot(targetId.value)
    if (data.error) {
      ElMessage.error('抓取失败: ' + data.error)
    } else {
      ElMessage.success(`快照抓取成功 (${formatBytes(data.file_size)})!`)
      await loadSnapshots()
      await loadTarget()
    }
  } catch (e) {
    ElMessage.error('快照抓取失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    capturingSnapshot.value = false
  }
}

function openWaybackPreview(snap) {
  currentSnap.value = snap
  showWaybackDialog.value = true
}

function openSnapInNewWindow(snapId) {
  if (!snapId) return
  window.open(api.getSnapshotViewUrl(snapId), '_blank')
}

async function viewSource(snap) {
  try {
    const resp = await fetch(api.getSnapshotRawUrl(snap.id))
    rawSourceCode.value = await resp.text()
    showSourceDialog.value = true
  } catch (e) {
    ElMessage.error('获取快照源码失败')
  }
}

function copyRawHtml() {
  if (!rawSourceCode.value) return
  navigator.clipboard.writeText(rawSourceCode.value)
  ElMessage.success('源码已复制到剪贴板')
}

async function showDiff(snap) {
  try {
    const { data } = await api.getSnapshotDiff(snap.id)
    diffData.value = data
    showDiffDialog.value = true
  } catch (e) {
    ElMessage.error('获取快照对比失败')
  }
}

function downloadSnap(snap) {
  window.open(api.getSnapshotDownloadUrl(snap.id), '_blank')
}

async function deleteSnap(snap) {
  try {
    await api.deleteSnapshot(snap.id)
    ElMessage.success('快照已删除')
    await loadSnapshots()
  } catch (e) {
    ElMessage.error('删除快照失败')
  }
}

onMounted(async () => {
  await Promise.all([loadTarget(), loadStats(), loadChecks(), loadScreenshots(), loadSnapshots()])
  loading.value = false
})

watch(activeTab, (tab) => {
  if (tab === 'checks') loadChecks()
  else if (tab === 'screenshots') loadScreenshots()
  else if (tab === 'snapshots') loadSnapshots()
})
</script>
