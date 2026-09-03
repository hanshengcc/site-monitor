# Site Monitor - 站点监控平台

大规模域名健康检测 + 页面截图 + 渲染异常识别 + 多渠道告警

## 功能

- **域名管理**：单个添加 / 批量导入（支持万级）、分组、启停
- **HTTP 检测**：状态码、响应时间、关键字匹配、定时自动检测
- **页面截图**：Playwright + Chromium 真实浏览器渲染，全页截图 + 缩略图
- **渲染异常识别**：白屏检测、错误关键字、控制台错误、空页面等多信号融合
- **告警推送**：Webhook / 钉钉 / 飞书，连续失败去抖
- **管理后台**：仪表盘、目标列表、检测历史、截图浏览、异常处理

## 架构

```
FastAPI (Python)     ← 单体后端，含 API + 调度 + 检测 + 截图
├── httpx            ← 异步 HTTP 探测 (200并发)
├── Playwright       ← 浏览器截图 (8并发)
├── Pillow + numpy   ← 图像异常分析
├── APScheduler      ← 定时调度
└── PostgreSQL       ← 数据存储 (按月分区)

Vue 3 + Element Plus ← 前端 SPA
```

## 快速启动

### 方式一：Docker Compose（推荐）

```bash
# 先构建前端
cd frontend && npm install && npm run build && cd ..

# 启动
docker-compose up -d --build

# 访问 http://localhost:8080
```

### 方式二：开发模式

```bash
# 启动 PostgreSQL
docker run -d --name site-monitor-db \
  -e POSTGRES_DB=site_monitor \
  -e POSTGRES_USER=monitor \
  -e POSTGRES_PASSWORD=monitor123 \
  -p 5432:5432 \
  -v $(pwd)/init.sql:/docker-entrypoint-initdb.d/init.sql \
  postgres:16-alpine

# 安装依赖
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 启动后端
export DATABASE_URL="postgresql+asyncpg://monitor:monitor123@localhost:5432/site_monitor"
uvicorn backend.app.main:app --host 0.0.0.0 --port 8080 --reload

# 启动前端开发服务器（另一个终端）
cd frontend && npm run dev
```

## API 概览

| 端点 | 说明 |
|---|---|
| `GET /api/dashboard/stats` | 仪表盘统计 |
| `GET /api/targets` | 目标列表（分页、搜索、筛选） |
| `POST /api/targets` | 添加目标 |
| `POST /api/targets/batch` | 批量导入 |
| `GET /api/targets/{id}` | 目标详情 |
| `GET /api/results/{target_id}` | 检测历史 |
| `GET /api/results/{target_id}/stats` | 可用率统计 |
| `GET /api/screenshots/{target_id}` | 截图列表 |
| `GET /api/screenshots/image/{id}` | 截图文件 |
| `GET /api/anomalies` | 异常列表 |
| `PUT /api/anomalies/{id}` | 更新异常状态 |
| `POST /api/tasks/check-all` | 手动触发全量检测 |
| `POST /api/tasks/screenshot-all` | 手动触发全量截图 |
| `POST /api/tasks/check/{id}` | 单个目标检测 |
| `POST /api/tasks/screenshot/{id}` | 单个目标截图 |

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://monitor:monitor123@localhost:5432/site_monitor` | 数据库连接 |
| `SCREENSHOTS_DIR` | `./screenshots` | 截图存储目录 |
| `CHECK_INTERVAL_MINUTES` | `5` | HTTP 检测间隔（分钟）|
| `SCREENSHOT_INTERVAL_MINUTES` | `360` | 截图间隔（分钟）|
| `PLAYWRIGHT_CONCURRENCY` | `8` | 截图并发数 |
| `MAX_CONCURRENT_CHECKS` | `200` | HTTP 检测并发数 |

## 异常检测规则

| 规则 | 权重 | 说明 |
|---|---|---|
| `keyword_in_title` | 40 | 页面标题含错误关键字（502/404/域名过期等） |
| `empty_page` | 30 | DOM 文本长度 < 50 |
| `solid_color` | 50 | 像素标准差 < 5（白屏/黑屏） |
| `uniform_screen` | 40 | >95% 像素为同一颜色 |
| `console_errors` | ≤20 | 浏览器控制台错误数 > 10 |
| `failed_requests` | ≤15 | HTTP 失败请求数 > 5 |

评分 ≥ 30 判定为异常，触发告警。

## 容量参考

| 指标 | 数值 |
|---|---|
| 2万域名 HTTP 检测 | ~2 分钟/轮（200并发）|
| 2万域名截图 | ~3 小时/轮（8并发）|
| 单日存储（2万×1次截图）| ~2GB |
| 推荐服务器 | 8C16G |
