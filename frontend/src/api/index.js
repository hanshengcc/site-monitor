import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

export default {
  // Dashboard
  getStats: () => api.get('/dashboard/stats'),
  getGroupStats: () => api.get('/dashboard/group-stats'),
  getRecentFailures: (limit = 20, group) => api.get('/dashboard/recent-failures', { params: { limit, group } }),

  // Targets
  getTargets: (params) => api.get('/targets', { params }),
  createTarget: (data) => api.post('/targets', data),
  batchCreate: (data) => api.post('/targets/batch', data),
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
  triggerCheckAll: () => api.post('/tasks/check-all'),
  getCheckProgress: () => api.get('/tasks/progress'),
  triggerScreenshotAll: () => api.post('/tasks/screenshot-all'),
  triggerCheck: (id) => api.post(`/tasks/check/${id}`),
  triggerScreenshot: (id) => api.post(`/tasks/screenshot/${id}`),
}
