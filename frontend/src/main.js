import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router.js'

// Register only the icons the app actually renders. Importing the whole icon
// package pulls ~300 components into the bundle and registers every one of them
// on startup, for the 16 that are used.
import {
  ArrowDown, Camera, Clock, DataBoard, Delete, Download, Link, Loading,
  Monitor, Picture, Plus, Refresh, Search, Setting, Upload, WarningFilled,
} from '@element-plus/icons-vue'

const icons = {
  ArrowDown, Camera, Clock, DataBoard, Delete, Download, Link, Loading,
  Monitor, Picture, Plus, Refresh, Search, Setting, Upload, WarningFilled,
}

const app = createApp(App)

for (const [name, component] of Object.entries(icons)) {
  app.component(name, component)
}

app.use(ElementPlus, { size: 'default' })
app.use(router)
app.mount('#app')
