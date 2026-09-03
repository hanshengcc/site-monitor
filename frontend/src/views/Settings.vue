<template>
  <div>
    <el-tabs v-model="activeTab">
      <!-- Tab 1: 定时检测与全局设置 -->
      <el-tab-pane label="检测设置" name="global">
        <el-card>
          <template #header><span style="font-weight: 600">⏱ 定时检测</span></template>
          <el-form :model="globalForm" label-width="160px" style="max-width: 600px">
            <el-form-item label="HTTP 检测间隔">
              <el-input-number v-model="globalForm.check_interval" :min="30" :step="30" />
              <span style="margin-left: 8px; color: #999">秒 ({{ formatInterval(globalForm.check_interval) }})</span>
            </el-form-item>
            <el-form-item label="截图间隔">
              <el-input-number v-model="globalForm.screenshot_interval" :min="600" :step="600" />
              <span style="margin-left: 8px; color: #999">秒 ({{ formatInterval(globalForm.screenshot_interval) }})</span>
            </el-form-item>
            <el-form-item label="最大检测并发">
              <el-input-number v-model="globalForm.max_concurrent_checks" :min="10" :max="1000" :step="50" />
            </el-form-item>
            <el-form-item label="截图并发">
              <el-input-number v-model="globalForm.playwright_concurrency" :min="1" :max="32" />
            </el-form-item>
            <el-form-item label="连续失败告警阈值">
              <el-input-number v-model="globalForm.consecutive_fails_threshold" :min="1" :max="20" />
              <span style="margin-left: 8px; color: #999">连续失败 N 次才发告警</span>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card style="margin-top: 16px">
          <template #header><span style="font-weight: 600">🌐 默认请求参数</span></template>
          <el-form :model="globalForm" label-width="160px" style="max-width: 600px">
            <el-form-item label="默认协议">
              <el-select v-model="globalForm.default_protocol" style="width: 120px">
                <el-option label="HTTPS" value="https" />
                <el-option label="HTTP" value="http" />
              </el-select>
            </el-form-item>
            <el-form-item label="默认超时">
              <el-input-number v-model="globalForm.default_timeout" :min="3" :max="60" />
              <span style="margin-left: 8px; color: #999">秒</span>
            </el-form-item>
            <el-form-item label="默认 User-Agent">
              <el-select v-model="globalForm.default_user_agent" filterable allow-create
                placeholder="选择预设或输入自定义 UA" style="width: 100%">
                <el-option-group label="📡 监控专用">
                  <el-option label="SiteMonitor/1.0" value="SiteMonitor/1.0" />
                  <el-option label="Mozilla/5.0 (compatible; SiteMonitor/1.0)" value="Mozilla/5.0 (compatible; SiteMonitor/1.0; +https://monitor.example.com)" />
                </el-option-group>
                <el-option-group label="🌐 桌面浏览器">
                  <el-option v-for="ua in uaPresets.desktop" :key="ua.value" :label="ua.label" :value="ua.value" />
                </el-option-group>
                <el-option-group label="📱 移动浏览器">
                  <el-option v-for="ua in uaPresets.mobile" :key="ua.value" :label="ua.label" :value="ua.value" />
                </el-option-group>
                <el-option-group label="🕷 搜索引擎蜘蛛">
                  <el-option v-for="ua in uaPresets.spider" :key="ua.value" :label="ua.label" :value="ua.value" />
                </el-option-group>
              </el-select>
              <div style="margin-top: 6px; font-size: 12px; color: #999; word-break: break-all; line-height: 1.6">
                当前: <code>{{ globalForm.default_user_agent }}</code>
              </div>
            </el-form-item>
            <el-form-item label="跟随重定向">
              <el-switch v-model="globalForm.default_follow_redirects" />
            </el-form-item>
            <el-form-item label="验证 SSL">
              <el-switch v-model="globalForm.default_verify_ssl" />
              <span style="margin-left: 8px; color: #999; font-size: 12px">关闭可监控自签名证书站点</span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveGlobal" :loading="saving">保存设置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <!-- Tab 2: 告警渠道 -->
      <el-tab-pane label="告警渠道" name="channels">
        <el-card>
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-weight: 600">📢 告警渠道</span>
              <el-button type="primary" size="small" @click="showAdd = true"><el-icon><Plus /></el-icon> 添加渠道</el-button>
            </div>
          </template>

          <el-table :data="channels" stripe size="small" empty-text="暂无告警渠道">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="name" label="名称" width="150" />
            <el-table-column prop="channel_type" label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="typeTag(row.channel_type)">{{ typeLabel(row.channel_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="配置" min-width="300">
              <template #default="{ row }">
                <template v-if="row.channel_type === 'telegram'">
                  <span style="font-size: 12px">Bot: <code>{{ maskToken(row.config.bot_token) }}</code> Chat: <code>{{ row.config.chat_id }}</code></span>
                </template>
                <template v-else>
                  <code style="font-size: 12px; word-break: break-all">{{ row.config.url }}</code>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" align="center">
              <template #default="{ row }">
                <el-button link size="small" @click="doTest(row)">测试</el-button>
                <el-button link size="small" type="danger" @click="doDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- Add Channel Dialog -->
        <el-dialog v-model="showAdd" title="添加告警渠道" width="520px">
          <el-form :model="addForm" label-width="100px">
            <el-form-item label="名称"><el-input v-model="addForm.name" placeholder="如: 运维群告警" /></el-form-item>
            <el-form-item label="类型">
              <el-select v-model="addForm.channel_type" style="width: 100%" @change="onTypeChange">
                <el-option label="Telegram" value="telegram" />
                <el-option label="Webhook" value="webhook" />
                <el-option label="钉钉机器人" value="dingtalk" />
                <el-option label="飞书机器人" value="feishu" />
              </el-select>
            </el-form-item>
            <template v-if="addForm.channel_type === 'telegram'">
              <el-form-item label="Bot Token">
                <el-input v-model="addForm.bot_token" placeholder="123456:ABC-DEF..." />
                <div style="font-size: 12px; color: #999; margin-top: 4px">从 <a href="https://t.me/BotFather" target="_blank" style="color: #409eff">@BotFather</a> 获取</div>
              </el-form-item>
              <el-form-item label="Chat ID">
                <el-input v-model="addForm.chat_id" placeholder="-1001234567890" />
              </el-form-item>
            </template>
            <template v-else>
              <el-form-item label="Webhook URL"><el-input v-model="addForm.url" placeholder="https://..." /></el-form-item>
            </template>
          </el-form>
          <template #footer>
            <el-button @click="showAdd = false">取消</el-button>
            <el-button type="primary" @click="doAdd">确定</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- Tab 3: 分组并发策略 -->
      <el-tab-pane label="分组策略" name="groups">
        <el-card>
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-weight: 600">⚙️ 分组并发策略</span>
              <span style="font-size: 12px; color: #999">每个分组可独立设置并发数、超时、UA，避免压垂目标服务器</span>
            </div>
          </template>

          <el-table :data="groupSettings" stripe size="small">
            <el-table-column prop="group_name" label="分组" min-width="140">
              <template #default="{ row }">
                <span style="font-weight: 600">{{ row.group_name }}</span>
                <el-tag size="small" type="info" style="margin-left: 6px">{{ row.total_targets }}个</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="组并发数" width="140" align="center">
              <template #default="{ row }">
                <el-input-number v-model="row.max_concurrency" :min="1" :max="500" size="small"
                  @change="onGroupChange(row)" />
              </template>
            </el-table-column>
            <el-table-column label="超时(秒)" width="130" align="center">
              <template #default="{ row }">
                <el-input-number v-model="row.request_timeout" :min="3" :max="120" size="small"
                  @change="onGroupChange(row)" />
              </template>
            </el-table-column>
            <el-table-column label="User-Agent" min-width="260">
              <template #default="{ row }">
                <el-select v-model="row.user_agent" filterable allow-create clearable size="small"
                  placeholder="继承全局设置" style="width: 100%" @change="onGroupChange(row)">
                  <el-option label="← 继承全局设置" :value="null" />
                  <el-option v-for="ua in quickUAs" :key="ua.value" :label="ua.label" :value="ua.value" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="70" align="center">
              <template #default="{ row }">
                <el-switch v-model="row.enabled" size="small" @change="onGroupChange(row)" />
              </template>
            </el-table-column>
            <el-table-column label="备注" width="160">
              <template #default="{ row }">
                <el-input v-model="row.note" size="small" placeholder="备注" @change="onGroupChange(row)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70" align="center">
              <template #default="{ row }">
                <el-button v-if="row._dirty" link type="primary" size="small" @click="saveGroup(row)">保存</el-button>
                <el-tag v-else type="success" size="small">✓</el-tag>
              </template>
            </el-table-column>
          </el-table>

          <div style="margin-top: 16px; padding: 12px; background: #f5f7fa; border-radius: 6px; font-size: 13px; color: #666; line-height: 2">
            <b>说明:</b><br/>
            • <b>组并发数</b>: 该分组内最多同时进行的检测数。同一服务器/CDN 上的站点建议设为 <b>5~20</b>，避免触发限流。<br/>
            • <b>超时</b>: 该组的 HTTP 请求超时秒数。响应慢的站点可调大到 30~60秒。<br/>
            • <b>User-Agent</b>: 留空则继承全局设置，也可为每组单独指定。<br/>
            • <b>全局并发</b>仍受“检测设置”页的上限控制，分组并发在此范围内生效。
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api/index.js'

const activeTab = ref('global')
const saving = ref(false)

// ---- UA Presets ----
const uaPresets = {
  desktop: [
    { label: 'Chrome 136 (Windows)', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36' },
    { label: 'Chrome 136 (macOS)', value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36' },
    { label: 'Chrome 136 (Linux)', value: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36' },
    { label: 'Firefox 138 (Windows)', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0' },
    { label: 'Firefox 138 (macOS)', value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:138.0) Gecko/20100101 Firefox/138.0' },
    { label: 'Safari 18 (macOS)', value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15' },
    { label: 'Edge 136 (Windows)', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0' },
  ],
  mobile: [
    { label: 'Chrome Mobile (Android)', value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36' },
    { label: 'Safari Mobile (iPhone)', value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1' },
    { label: 'Samsung Browser (Android)', value: 'Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/125.0.0.0 Mobile Safari/537.36' },
    { label: 'WeChat Browser (Android)', value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.47' },
  ],
  spider: [
    { label: 'Googlebot', value: 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' },
    { label: 'Googlebot (Mobile)', value: 'Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' },
    { label: 'Bingbot', value: 'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)' },
    { label: 'Baiduspider', value: 'Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)' },
    { label: 'Baiduspider (Mobile)', value: 'Mozilla/5.0 (Linux; U; Android 4.1; zh-CN; ) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 UCBrowser/1.0.0.0 U4/0.8.0 Mobile Safari/534.30 Baiduspider/2.0' },
    { label: 'YandexBot', value: 'Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)' },
    { label: 'Sogou Spider', value: 'Sogou web spider/4.0 (+http://www.sogou.com/docs/help/webmasters.htm#07)' },
    { label: '360Spider', value: 'Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0); 360Spider' },
    { label: 'Bytespider (TikTok)', value: 'Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)' },
    { label: 'DuckDuckBot', value: 'DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)' },
    { label: 'Slurp (Yahoo)', value: 'Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)' },
    { label: 'ChatGPT-User', value: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ChatGPT-User/1.0; +https://openai.com/bot)' },
    { label: 'ClaudeBot', value: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)' },
    { label: 'GPTBot (OpenAI)', value: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)' },
  ],
}

// ---- Global Settings ----
const globalForm = ref({
  check_interval: 300,
  screenshot_interval: 21600,
  max_concurrent_checks: 200,
  playwright_concurrency: 8,
  consecutive_fails_threshold: 3,
  default_protocol: 'https',
  default_timeout: 15,
  default_user_agent: 'SiteMonitor/1.0',
  default_follow_redirects: true,
  default_verify_ssl: false,
})

function formatInterval(seconds) {
  if (!seconds) return '-'
  if (seconds < 60) return `${seconds}秒`
  if (seconds < 3600) return `${Math.round(seconds / 60)}分钟`
  return `${(seconds / 3600).toFixed(1)}小时`
}

async function loadGlobalSettings() {
  try {
    const { data } = await api.getGlobalSettings()
    // Merge loaded values (they're stored as JSON values)
    for (const [k, v] of Object.entries(data)) {
      if (k in globalForm.value) {
        const parsed = typeof v === 'string' ? (isNaN(v) ? v.replace(/^"|"$/g, '') : Number(v)) : v
        globalForm.value[k] = parsed
      }
    }
  } catch (e) {}
}

async function saveGlobal() {
  saving.value = true
  try {
    await api.updateGlobalSettings(globalForm.value)
    ElMessage.success('设置已保存，定时任务已更新')
  } catch (e) {
    ElMessage.error('保存失败')
  }
  saving.value = false
}

// ---- Alert Channels ----
const channels = ref([])
const showAdd = ref(false)
const addForm = ref({ name: '', channel_type: 'telegram', url: '', bot_token: '', chat_id: '' })

function typeLabel(t) { return { webhook: 'Webhook', telegram: 'Telegram', dingtalk: '钉钉', feishu: '飞书' }[t] || t }
function typeTag(t) { return { webhook: 'info', telegram: 'primary', dingtalk: 'warning', feishu: 'success' }[t] || 'info' }
function maskToken(token) { return token ? token.substring(0, 8) + '...' + token.slice(-4) : '' }
function onTypeChange() { addForm.value.url = ''; addForm.value.bot_token = ''; addForm.value.chat_id = '' }

async function loadChannels() {
  try { const { data } = await api.getChannels(); channels.value = data } catch (e) {}
}

// ---- Group Settings ----
const groupSettings = ref([])

const quickUAs = [
  { label: 'Chrome (Windows)', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36' },
  { label: 'Chrome (Mobile)', value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36' },
  { label: 'Googlebot', value: 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' },
  { label: 'Baiduspider', value: 'Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)' },
  { label: 'SiteMonitor/1.0', value: 'SiteMonitor/1.0' },
]

async function loadGroupSettings() {
  try {
    const { data } = await api.getGroupSettings()
    groupSettings.value = data.map(g => ({ ...g, _dirty: false }))
  } catch (e) {}
}

function onGroupChange(row) {
  row._dirty = true
}

async function saveGroup(row) {
  try {
    await api.updateGroupSetting(row.group_name, {
      max_concurrency: row.max_concurrency,
      request_timeout: row.request_timeout,
      user_agent: row.user_agent || null,
      enabled: row.enabled,
      note: row.note || null,
    })
    row._dirty = false
    ElMessage.success(`分组 [${row.group_name}] 设置已保存`)
  } catch (e) {
    ElMessage.error('保存失败')
  }
}

async function doAdd() {
  if (!addForm.value.name) return ElMessage.warning('请填写名称')
  let config = {}
  if (addForm.value.channel_type === 'telegram') {
    if (!addForm.value.bot_token || !addForm.value.chat_id) return ElMessage.warning('请填写 Bot Token 和 Chat ID')
    config = { bot_token: addForm.value.bot_token, chat_id: addForm.value.chat_id }
  } else {
    if (!addForm.value.url) return ElMessage.warning('请填写 Webhook URL')
    config = { url: addForm.value.url }
  }
  await api.createChannel({ name: addForm.value.name, channel_type: addForm.value.channel_type, config })
  ElMessage.success('添加成功')
  showAdd.value = false
  addForm.value = { name: '', channel_type: 'telegram', url: '', bot_token: '', chat_id: '' }
  loadChannels()
}

async function doTest(row) {
  try { await api.testChannel(row.id); ElMessage.success('测试消息已发送') }
  catch (e) { ElMessage.error('发送失败: ' + (e.response?.data?.detail || e.message)) }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除渠道 ${row.name}?`, '确认', { type: 'warning' })
    await api.deleteChannel(row.id)
    ElMessage.success('已删除')
    loadChannels()
  } catch (e) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

onMounted(() => { loadGlobalSettings(); loadChannels(); loadGroupSettings() })
</script>
