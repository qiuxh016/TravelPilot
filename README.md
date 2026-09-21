# TravelPilot

TravelPilot 是面向国内自由行的旅行规划 MVP。它把攻略文本变成待确认地点，保存 Wishlist，以地图路程和时间约束生成版本化 Plan，并在天气、疲劳、取消和延误等事件后局部重规划。

## 当前能力

- Trip 与 Wishlist CRUD；Fake/高德 POI 搜索、地点详情、路线和距离矩阵；本地 10 分钟查询缓存。
- 确定性规划：按地点粗分组、日期分配、近邻排序，再检查交通、营业时间、每日数量和末尾缓冲。每次生成都是新 Plan 版本；节点编辑和删除同样生成新版本。
- 攻略文本导入：可选兼容 Chat Completions 的模型做结构化抽取；未配置模型时用保守本地抽取。所有候选都需要用户确认后才进入 Wishlist。
- Planner Agent：读取 Trip、Wishlist 和已确认记忆；把“轻松/紧凑”等偏好转换为排程约束，通过受限 Tool Port 查询地图矩阵，并记录 Agent 和 Tool trace。
- 事件重规划：只处理事件当天及之后的非固定节点，保留旧版本并返回 KEEP/ADD/REMOVE/MOVE/UPDATE Diff。暴雨时优先用已收藏的室内地点替换；无候选时展示警告。
- 反馈与记忆：反馈生成待确认记忆，支持确认、修改和删除；仅已确认记忆进入 Agent 上下文。
- Redis Worker：可异步消费重规划任务，查询任务状态。API 也保留同步 `/replan-runs` 方便交互演示。

网页为响应式 Web 应用，可在桌面和手机浏览器访问；尚无原生移动 App。Fake 地图与本地抽取可离线演示；真实高德、外部模型和天气 Provider 需要另外配置并验证。天气 Tool 在未配置 Provider 时明确返回 `UNAVAILABLE`。

## 结构

| 目录 | 职责 |
| --- | --- |
| `apps/api` | FastAPI 路由、请求与响应 Schema、请求日志 |
| `apps/web` | Next.js 页面与 API client |
| `apps/worker` | Redis 后台重规划任务 |
| `packages/core` | 纯排程、约束、影响分析、Agent Runtime 与 Tool Schema |
| `packages/infrastructure` | PostgreSQL、Alembic、地图与 MCP 适配器 |
| `tests/evaluation` | 八个固定离线场景和指标计算 |

## 本地启动（Windows PowerShell）

需要 Python 3.12+、Node.js 20+、pnpm、Docker Desktop。以下命令均在仓库根目录执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm install
Copy-Item .env.example .env
docker compose up -d postgres redis
.\.venv\Scripts\alembic.exe upgrade head
```

如果主机 `6379` 已被占用，可在未提交的 `.env` 中设 `REDIS_PORT=6380`、`REDIS_URL=redis://localhost:6380/0`，再执行 `docker compose up -d redis`。不要停止占用该端口的其他服务。高德需要在 `.env` 中设置 `MAPS_PROVIDER=amap` 和 `AMAP_API_KEY`；默认 `fake` 不需要 Key。可选文本模型使用 `LLM_API_KEY`、`LLM_MODEL` 和 `LLM_BASE_URL`。不要提交真实凭据。

分别在三个 PowerShell 窗口运行：

```powershell
.\.venv\Scripts\uvicorn.exe travelpilot_api.main:app --reload --port 8000
pnpm --dir apps/web dev
.\.venv\Scripts\python.exe -m travelpilot_worker.main
```

Web 默认 `http://localhost:3000`，API 文档为 `http://localhost:8000/docs`。如果 Web 使用 `3001`，API 已允许该本地端口。使用隔离 Redis 时，Worker 可传 `--redis-url redis://localhost:6380/0`。

## 演示流程

1. 在 `/trips` 创建旅行，进入详情页。
2. 进入“导入攻略”，粘贴“想去宽窄巷子和熊猫基地”，勾选候选并确认；或在“搜索 POI”加入地点。
3. 在“查看计划”生成 Plan v1，再次生成得到 v2；旧版本仍可查询。
4. 在事件页输入旅行日期内的生效时间，选择天气变化、疲劳、地点取消或延误，查看新版本与 Diff。
5. 在旅行记忆页提交“今天很累，希望下午少安排景点”，确认候选记忆；Planner Agent 后续会读取它。

API 入口包括 `/api/v1/trips`、`/api/v1/trips/{trip_id}/wishlist`、`/api/v1/places/search`、`/api/v1/routes/*`、`/api/v1/trips/{trip_id}/plan-runs`、`/api/v1/trips/{trip_id}/content`、`/api/v1/content/{content_id}/confirm`、`/api/v1/trips/{trip_id}/agent-plan-runs`、`/api/v1/trips/{trip_id}/events`、`/api/v1/trips/{trip_id}/replan-runs`、`/api/v1/trips/{trip_id}/replan-jobs` 和 `/api/v1/trips/{trip_id}/feedback`。完整 Schema 见 Swagger。

## 验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q apps packages
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
pnpm --dir apps/web build
```

启动 PostgreSQL 并完成迁移后，可运行会自动清理测试 Trip 和 POI 的真实闭环：

```powershell
.\.venv\Scripts\python.exe scripts\verify_live.py
```

Redis 位于 `6380` 时运行 Worker 闭环：

```powershell
.\.venv\Scripts\python.exe scripts\verify_worker.py
```

启动 API 和 Web 后运行浏览器测试：

```powershell
pnpm --dir apps/web exec playwright install chromium
pnpm --dir apps/web exec playwright test
```

`tests/evaluation/cases/scenarios.json` 和 [评测说明](docs/research/evaluation.md) 是固定离线逻辑评测，不代表外部地图、天气或模型质量。项目尚未实现自动购票、支付、爬虫或生产级部署。
