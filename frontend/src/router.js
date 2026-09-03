import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('./views/Dashboard.vue') },
  { path: '/targets', name: 'Targets', component: () => import('./views/Targets.vue') },
  { path: '/targets/:id', name: 'TargetDetail', component: () => import('./views/TargetDetail.vue') },
  { path: '/anomalies', name: 'Anomalies', component: () => import('./views/Anomalies.vue') },
  { path: '/settings', name: 'Settings', component: () => import('./views/Settings.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
