<template>
  <div>
    <!-- Toolbar -->
    <el-row :gutter="12" style="margin-bottom: 16px" align="middle">
      <el-col :span="6">
        <el-input v-model="search" placeholder="搜索URL或名称" clearable @clear="loadData" @keyup.enter="loadData">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </el-col>
      <el-col :span="3">
        <el-select v-model="filterGroup" placeholder="分组" clearable @change="loadData">
          <el-option v-for="g in groups" :key="g.group" :label="`${g.group} (${g.count})`" :value="g.group" />
        </el-select>
      </el-col>
      <el-col :span="3">
        <el-select v-model="filterStatus" placeholder="状态" clearable @change="loadData">
          <el-option label="健康" value="ok" /><el-option label="异常" value="fail" /><el-option label="未知" value="unknown" />
        </el-select>
      </el-col>
      <el-col :span="3" v-if="filterGroup">
        <el-button size="small" type="warning" plain @click="checkCurrentGroup">
          <el-icon><Refresh /></el-icon> 检测该组
        </el-button>
      </el-col>
      <el-col :span="filterGroup ? 9 : 12" style="text-align: right">
        <el-button type="primary" @click="showAdd = true"><el-icon><Plus /></el-icon> 添加</el-button>
        <el-button @click="showBatch = true"><el-icon><Upload /></el-icon> 批量导入</el-button>
        <el-button type="success" @click="doExport"><el-icon><Download /></el-icon> 导出</el-button>
      </el-col>
    </el-row>

    <!-- Table -->
    <el-card>
      <el-table :data="targets" stripe size="small" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="站点" min-width="280">
          <template #default="{ row }">
            <div>
              <router-link :to="`/targets/${row.id}`" style="color: #409eff; text-decoration: none; font-weight: 500">
                {{ row.name || row.url }}
              </router-link>
            </div>
            <div style="color: #999; font-size: 12px">{{ row.url }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="group" label="分组" width="100" />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.status?.is_ok === true" type="success" size="small">正常</el-tag>
            <el-tag v-else-if="row.status?.is_ok === false" type="danger" size="small">异常</el-tag>
            <el-tag v-else type="info" size="small">未知</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态码" width="70" align="center">
          <template #default="{ row }">{{ row.status?.last_status_code || '-' }}</template>
        </el-table-column>
        <el-table-column label="延迟" width="80" align="center">
          <template #default="{ row }">
            <span v-if="row.status?.last_latency_ms">{{ row.status.last_latency_ms }}ms</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="连续失败" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.status?.consecutive_fails > 0" type="danger" size="small">
              {{ row.status.consecutive_fails }}
            </el-tag>
            <span v-else>0</span>
          </template>
        </el-table-column>
        <el-table-column label="渲染" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.status?.has_anomaly" type="warning" size="small">异常</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" size="small" @change="toggleTarget(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center">
          <template #default="{ row }">
            <el-button link size="small" @click="doCheck(row)" :loading="checkingId === row.id">检测</el-button>
            <el-button link size="small" @click="doShot(row)" :loading="shottingId === row.id">截图</el-button>
            <el-button link size="small" type="danger" @click="doDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page" v-model:page-size="size"
        :total="total" :page-sizes="[50, 100, 200]"
        layout="total, sizes, prev, pager, next" style="margin-top: 16px; justify-content: flex-end"
        @current-change="loadData" @size-change="loadData"
      />
    </el-card>

    <!-- Add Dialog -->
    <el-dialog v-model="showAdd" title="添加监控目标" width="500px">
      <el-form :model="addForm" label-width="80px">
        <el-form-item label="URL"><el-input v-model="addForm.url" placeholder="https://example.com" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="addForm.name" placeholder="可选" /></el-form-item>
        <el-form-item label="分组"><el-input v-model="addForm.group" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="doAdd">确定</el-button>
      </template>
    </el-dialog>

    <!-- Batch Import Dialog -->
    <el-dialog v-model="showBatch" title="批量导入" width="600px">
      <el-form :model="batchForm" label-width="80px">
        <el-form-item label="URL列表">
          <el-input v-model="batchForm.text" type="textarea" :rows="10" placeholder="每行一个URL" />
        </el-form-item>
        <el-form-item label="分组"><el-input v-model="batchForm.group" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showBatch = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doBatchImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api/index.js'

const targets = ref([])
const loading = ref(false)
const page = ref(1)
const size = ref(50)
const total = ref(0)
const search = ref('')
const filterGroup = ref('')
const filterStatus = ref('')
const groups = ref([])

const showAdd = ref(false)
const addForm = ref({ url: '', name: '', group: 'default' })

const showBatch = ref(false)
const batchForm = ref({ text: '', group: 'default' })
const importing = ref(false)
const checkingId = ref(null)
const shottingId = ref(null)

async function loadData() {
  loading.value = true
  try {
    const { data } = await api.getTargets({
      page: page.value, size: size.value,
      search: search.value || undefined,
      group: filterGroup.value || undefined,
      status: filterStatus.value || undefined,
    })
    targets.value = data.items
    total.value = data.total
  } catch (e) {
    ElMessage.error('加载失败')
  }
  loading.value = false
}

async function loadGroups() {
  try {
    const { data } = await api.getGroups()
    groups.value = data
  } catch (e) {}
}

async function doAdd() {
  if (!addForm.value.url) return ElMessage.warning('请输入URL')
  await api.createTarget(addForm.value)
  ElMessage.success('添加成功')
  showAdd.value = false
  addForm.value = { url: '', name: '', group: 'default' }
  loadData()
}

async function doBatchImport() {
  const urls = batchForm.value.text.split('\n').map(s => s.trim()).filter(Boolean)
  if (!urls.length) return ElMessage.warning('请输入URL')
  importing.value = true
  try {
    const { data } = await api.batchCreate({ urls, group: batchForm.value.group })
    ElMessage.success(`导入完成: 新增 ${data.created}, 跳过 ${data.skipped}`)
    showBatch.value = false
    batchForm.value = { text: '', group: 'default' }
    loadData()
    loadGroups()
  } catch (e) {
    ElMessage.error('导入失败: ' + (e.response?.data?.detail || e.message || '超时'))
  } finally {
    importing.value = false
  }
}

async function checkCurrentGroup() {
  if (!filterGroup.value) return
  try {
    await api.triggerCheckAll(filterGroup.value)
    ElMessage.success(`已触发分组 [${filterGroup.value}] 检测`)
  } catch (e) {
    ElMessage.error('触发失败')
  }
}

function doExport() {
  const url = api.exportTargets({
    search: search.value || undefined,
    group: filterGroup.value || undefined,
    status: filterStatus.value || undefined,
  })
  window.open(url, '_blank')
}

async function toggleTarget(row) {
  await api.toggleTarget(row.id)
}

async function doCheck(row) {
  checkingId.value = row.id
  try {
    const { data } = await api.triggerCheck(row.id)
    if (data.is_ok) ElMessage.success(`${row.name || row.url}: 正常 (${data.latency_ms}ms)`)
    else ElMessage.error(`${row.name || row.url}: ${data.error}`)
    loadData()
  } catch (e) {
    ElMessage.error('检测失败: ' + (e.message || '超时'))
  }
  checkingId.value = null
}

async function doShot(row) {
  shottingId.value = row.id
  try {
    ElMessage.info('截图中，请稍候...')
    await api.triggerScreenshot(row.id)
    ElMessage.success('截图完成')
  } catch (e) {
    ElMessage.error('截图失败')
  }
  shottingId.value = null
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除 ${row.name || row.url}?`, '确认', { type: 'warning' })
    await api.deleteTarget(row.id)
    ElMessage.success('已删除')
    loadData()
  } catch (e) {
    if (e !== 'cancel' && e?.toString() !== 'cancel') {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

onMounted(() => { loadData(); loadGroups() })
</script>
