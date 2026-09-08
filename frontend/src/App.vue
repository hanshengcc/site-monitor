<template>
  <el-container style="height: 100vh">
    <!-- Sidebar -->
    <el-aside width="220px" style="background: #1d1e1f">
      <div style="padding: 20px; text-align: center; color: #fff">
        <el-icon :size="28"><Monitor /></el-icon>
        <h3 style="margin: 8px 0 0">站点监控</h3>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#1d1e1f"
        text-color="#bbb"
        active-text-color="#409eff"
      >
        <el-menu-item index="/">
          <el-icon><DataBoard /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/targets">
          <el-icon><Link /></el-icon>
          <span>监控目标</span>
        </el-menu-item>
        <el-menu-item index="/anomalies">
          <el-icon><WarningFilled /></el-icon>
          <span>异常告警</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>系统设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- Main -->
    <el-container>
      <el-header style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #eee">
        <span style="font-size: 16px; font-weight: 500; color: #333">{{ $route.name }}</span>
        <div style="display: flex; align-items: center; gap: 12px">
          <!-- Progress bar -->
          <div v-if="progress.running || waitingStart" style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: #666">
            <template v-if="progress.running">
              <span v-if="progress.group" style="font-weight: 600; color: #409eff; font-size: 12px">[{{ progress.group }}]</span>
              <el-progress
                :percentage="progressPct"
                :stroke-width="16"
                :text-inside="true"
                :format="() => `${progress.done}/${progress.total}`"
                style="width: 180px"
              />
              <span style="color: #67c23a">✓{{ progress.ok }}</span>
              <span style="color: #f56c6c">✗{{ progress.fail }}</span>
            </template>
            <template v-else>
              <el-icon class="is-loading"><Loading /></el-icon>
              <span>正在准备检测任务...</span>
            </template>
          </div>

          <!-- Group Selectable Check Button -->
          <el-dropdown trigger="click" @command="handleCheckCommand" :disabled="progress.running || waitingStart">
            <el-button size="small" type="primary" :loading="checking" :disabled="progress.running || waitingStart">
              <el-icon><Refresh /></el-icon>
              {{ progress.running ? (progress.group ? `检测中 [${progress.group}]...` : '全量检测中...') : waitingStart ? '准备中...' : '立即检测' }}
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="__ALL__">
                  <span style="font-weight: 600; color: #409eff">🌐 全量检测 (全部站点)</span>
                </el-dropdown-item>
                <el-dropdown-item divided disabled>
                  <span style="font-size: 12px; color: #999">选择分组单独检测：</span>
                </el-dropdown-item>
                <el-dropdown-item v-for="g in groupList" :key="g.group" :command="g.group">
                  📁 {{ g.group }} ({{ g.count }} 个站点)
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <el-button size="small" @click="triggerScreenshotAll" :loading="shotting">
            <el-icon><Camera /></el-icon> 立即截图
          </el-button>
        </div>
      </el-header>
      <el-main style="background: #f5f7fa; overflow-y: auto">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, Refresh, Camera, Loading, Monitor, DataBoard, Link, WarningFilled, Setting } from '@element-plus/icons-vue'
import api from './api/index.js'

const checking = ref(false)
const shotting = ref(false)
const waitingStart = ref(false)
const progress = ref({ running: false, total: 0, done: 0, ok: 0, fail: 0, group: null })
const groupList = ref([])
let progressTimer = null
let pollCount = 0

const progressPct = computed(() => {
  if (!progress.value.total) return 0
  return Math.round(progress.value.done / progress.value.total * 100)
})

async function loadGroups() {
  try {
    const { data } = await api.getGroups()
    groupList.value = data || []
  } catch (e) {}
}

async function pollProgress() {
  try {
    const { data } = await api.getCheckProgress()
    progress.value = data

    if (data.running) {
      waitingStart.value = false
      pollCount = 0
    } else if (waitingStart.value) {
      pollCount++
      if (pollCount > 15) {
        stopPolling()
        waitingStart.value = false
        checking.value = false
        ElMessage.warning('检测任务启动超时，请重试')
      }
    } else if (data.total > 0) {
      stopPolling()
      checking.value = false
      const prefix = data.group ? `[${data.group}] ` : ''
      ElMessage.success(`${prefix}检测完成: ${data.ok} 正常, ${data.fail} 失败 (共${data.total})`)
      loadGroups()
    } else {
      stopPolling()
      checking.value = false
    }
  } catch (e) {}
}

function startPolling() {
  if (progressTimer) clearInterval(progressTimer)
  pollCount = 0
  progressTimer = setInterval(pollProgress, 2000)
}

function stopPolling() {
  if (progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
  }
}

async function handleCheckCommand(cmd) {
  const group = cmd === '__ALL__' ? null : cmd
  await runCheck(group)
}

async function runCheck(group = null) {
  checking.value = true
  waitingStart.value = true
  try {
    await api.triggerCheckAll(group)
    const label = group ? `分组 [${group}]` : '全量'
    ElMessage.info(`${label} 检测任务已启动，状态实时更新中...`)
    startPolling()
  } catch (e) {
    ElMessage.error('触发失败')
    checking.value = false
    waitingStart.value = false
  }
}

async function triggerScreenshotAll() {
  shotting.value = true
  try {
    await api.triggerScreenshotAll()
    ElMessage.success('已触发全量截图')
  } catch (e) {
    ElMessage.error('触发失败')
  }
  shotting.value = false
}

onMounted(async () => {
  loadGroups()
  try {
    const { data } = await api.getCheckProgress()
    progress.value = data
    if (data.running) {
      checking.value = true
      startPolling()
    }
  } catch (e) {}
})

onUnmounted(() => stopPolling())
</script>

<style>
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
.el-aside .el-menu { border-right: none; }
</style>
