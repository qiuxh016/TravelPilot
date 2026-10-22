# TravelPilot

TravelPilot 是一个面向自由行的旅行规划工具，帮助用户整理旅行地点、生成每日计划，并在旅行安排变化时查看和调整计划。

## 功能

- 多用户账户与旅行数据隔离，支持邮箱或用户名登录、注册时设置昵称、邮箱验证、头像管理、账户导出和删除。
- 创建旅行、维护 Wishlist，并按日期、地点和节奏生成版本化行程。
- 导入攻略文本或图片；图片文字可通过本地 OCR 提取，地点经用户确认后加入旅行。
- AI 旅行顾问通过受限工具调用检索并核验地点，支持多轮排程、草案预览与途中事件 What-if 模拟；按日查看安排、差异与约束提示，用户确认后才保存正式版本。
- 记录旅行事件、执行局部重规划，并通过反馈维护旅行记忆。
- 好友、只读分享和小团体协作旅行；成员可以实时讨论并同步行程版本，权限与数据按账户隔离。
- 按账户时区筛选即将出发、进行中和已完成的旅行；可设置出发前规划提醒和旅后记忆提醒。
- 旅行动态与图片使用受控访问；提醒可在账户设置中关闭或延后处理。

地图默认使用 Fake Provider；配置高德 Web 服务 Key 并设置 `MAPS_PROVIDER=amap` 后可切换为高德服务。行程计划页接入 Open-Meteo 预报，显示当前 16 天范围内的目的地天气、获取时间和数据署名；免费接口仅用于个人非商业试用。模型能力通过服务端配置，可选启用。

## 技术栈

- Web：Next.js、React、TypeScript
- API：FastAPI、Python、SQLAlchemy、Alembic
- 数据与任务：PostgreSQL、Redis、Redis Worker
- 部署与本地依赖：Docker Compose

## 本地运行

需要 Python 3.12+、Node.js 20+、pnpm 和 Docker Desktop。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm install
Copy-Item .env.example .env
docker compose up -d postgres redis
.\.venv\Scripts\alembic.exe upgrade head
```

分别在三个 PowerShell 窗口启动 API、Web 和后台 Worker：

```powershell
.\.venv\Scripts\python.exe -m uvicorn travelpilot_api.main:app --host 127.0.0.1 --port 8000
pnpm --dir apps/web dev
.\.venv\Scripts\python.exe -m travelpilot_worker.main
```

打开 Web 时以 Next.js 启动日志显示的地址为准（默认 `http://localhost:3000`；若端口被占用会自动选择其他端口）。API 文档：<http://localhost:8000/docs>。

本地开发环境的邮箱验证链接可从 `http://localhost:8000/api/v1/auth/dev-mailbox/{邮箱}` 查看。高德地图和文本模型为可选服务，在 `.env` 配置 `MAPS_PROVIDER=amap`、`AMAP_API_KEY`、`LLM_API_KEY`、`LLM_MODEL` 等变量即可。若希望顾问显示模型费用估算，可按当前供应商和模型价格设置 `LLM_INPUT_COST_PER_MILLION_USD` 与 `LLM_OUTPUT_COST_PER_MILLION_USD`；两项都留空时只显示 token 用量，不猜测价格。高德 Key 应创建为“Web 服务”类型。生产环境需将 `WEB_ALLOWED_ORIGINS` 设为精确的 HTTPS 前端源；若 Web 与 API 使用不同子域名，还需将 `SESSION_COOKIE_DOMAIN` 设为共同父域。不要提交 `.env` 或真实凭据。
