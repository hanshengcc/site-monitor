import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 120000 })

export default {
  // Dashboard
  getStats: () => api.get('/dashboard/stats'),
  getGroupStats: () => api.get('/dashboard/group-stats'),
  getSslSummary: (group) => api.get('/dashboard/ssl-summary', { params: { group } }),
  getRecentFailures: (group, errorTypes) => {
    const params = {}
    if (group) params.group = group
    // errorTypes is an array like ['timeout','connect_error']
    if (errorTypes && errorTypes.length) params.error_type = errorTypes
    return api.get('/dashboard/recent-failures', { params, paramsSerializer: { indexes: null } })
  },
  exportFailures: (group, errorTypes) => {
    let url = '/api/dashboard/export-failures?'
    const parts = []
    if (group) parts.push('group=' + encodeURIComponent(group))
    if (errorTypes && errorTypes.length) {
      errorTypes.forEach(t => parts.push('error_type=' + encodeURIComponent(t)))
    }
    return url + parts.join('&')
  },
  getSslOverview: (group, status, limit = 200) => api.get('/dashboard/ssl-overview', { params: { group, status, limit } }),
  exportSsl: (group, status) => {
    const parts = []
    if (group) parts.push('group=' + encodeURIComponent(group))
    if (status) parts.push('status=' + encodeURIComponent(status))
    return '/api/dashboard/ssl-export' + (parts.length ? '?' + parts.join('&') : '')
  },

  // Targets
  getTargets: (params) => api.get('/targets', { params }),
  createTarget: (data) => api.post('/targets', data),
  batchCreate: (data) => api.post('/targets/batch', data),
  exportTargets: (params = {}) => {
    const parts = []
    if (params.search) parts.push('search=' + encodeURIComponent(params.search))
    if (params.group) parts.push('group=' + encodeURIComponent(params.group))
    if (params.status) parts.push('status=' + encodeURIComponent(params.status))
    if (params.enabled !== undefined) parts.push('enabled=' + encodeURIComponent(params.enabled))
    return '/api/targets/export' + (parts.length ? '?' + parts.join('&') : '')
  },
  getTarget: (id) => api.get(`/targets/${id}`),
  updateTarget: (id, data) => api.put(`/targets/${id}`, data),
  deleteTarget: (id) => api.delete(`/targets/${id}`),
  toggleTarget: (id) => api.post(`/targets/${id}/toggle`),
  getGroups: () => api.get('/targets/groups/list'),

  // Results
  getResults: (targetId, params) => api.get(`/results/${targetId}`, { params }),
  getLatestResult: (targetId) => api.get(`/results/${targetId}/latest`),
  getStats24h: (targetId, hours = 24) => api.get(`/results/${targetId}/stats`, { params: { hours } }),

  // Screenshots
  getScreenshots: (targetId, params) => api.get(`/screenshots/${targetId}`, { params }),
  getScreenshotUrl: (id, thumb = false) => `/api/screenshots/image/${id}?thumb=${thumb}`,

  // Anomalies
  getAnomalies: (params) => api.get('/anomalies', { params }),
  updateAnomaly: (id, data) => api.put(`/anomalies/${id}`, data),

  // Alert Channels
  getChannels: () => api.get('/channels'),
  createChannel: (data) => api.post('/channels', data),
  deleteChannel: (id) => api.delete(`/channels/${id}`),
  testChannel: (id) => api.post(`/channels/${id}/test`),

  // Global Settings
  getGlobalSettings: () => api.get('/settings/global'),
  updateGlobalSettings: (data) => api.put('/settings/global', data),

  // Group Settings
  getGroupSettings: () => api.get('/settings/groups'),
  updateGroupSetting: (group, data) => api.put(`/settings/groups/${encodeURIComponent(group)}`, data),

  // Tasks
  triggerCheckAll: (group) => api.post('/tasks/check-all', null, { params: group ? { group } : {} }),
  triggerCheckGroup: (group) => api.post(`/tasks/check-group/${encodeURIComponent(group)}`),
  getSchedulerStatus: () => api.get('/tasks/scheduler-status'),
  getCheckProgress: () => api.get('/tasks/progress'),
  triggerScreenshotAll: () => api.post('/tasks/screenshot-all'),
  triggerCheck: (id) => api.post(`/tasks/check/${id}`),
  triggerScreenshot: (id) => api.post(`/tasks/screenshot/${id}`),
  triggerRetryFailed: (group) => api.post('/tasks/retry-failed', null, { params: { group } }),
  getRetryProgress: () => api.get('/tasks/retry-progress'),
}
