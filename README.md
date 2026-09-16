# TravelPilot

TravelPilot 是一个面向国内自由行的 Context-aware Multi-Agent Travel Copilot。

当前阶段目标：

```text
创建 Trip → 添加 Wishlist 地点 → 保存到 PostgreSQL → 前端查看
```

## 技术栈

- Web：Next.js 15、React、TypeScript
- API：Python、FastAPI、Uvicorn、Pydantic
- 数据库：PostgreSQL 17 + pgvector
- 缓存/队列：Redis 7
- ORM/迁移：SQLAlchemy、Alembic
- 测试：Pytest、Playwright（逐步接入）
- 工程：Docker Compose、Git、GitHub

## 项目结构

```text
TravelPilot/
├─ apps/
│  ├─ web/                         # Next.js 前端
│  │  ├─ app/                      # 首页、Trip 页面、样式
│  │  └─ lib/api.ts               # 前端 API Client
│  ├─ api/                         # FastAPI 后端
│  │  ├─ src/travelpilot_api/
│  │  │  ├─ main.py               # API 入口和 Router 注册
│  │  │  ├─ api/v1/               # Trip、Wishlist 路由
│  │  │  └─ schemas/              # Pydantic 模型
│  │  └─ tests/                   # 后端测试
│  └─ worker/                      # 后台任务入口
├─ packages/
│  ├─ core/                        # 领域和应用逻辑
│  ├─ infrastructure/             # 数据库和 Provider 适配器
│  └─ contracts/                  # API/Event 契约
├─ docs/                           # 架构文档和 ADR
├─ docker-compose.yml              # PostgreSQL + Redis
├─ pyproject.toml                  # Python 依赖和打包配置
├─ package.json                    # pnpm workspace 配置
├─ week1.md                        # Week 1 实施步骤
├─ TravelPilot_项目规划.md
└─ TravelPilot_项目结构与开源参考.md
```

## 环境要求

- Windows 10/11
- Python 3.12+
- Node.js 20+
- pnpm
- Docker Desktop（Linux Containers 模式）
- Git
- VS Code（推荐）

## 本地启动

### 安装依赖

```powershell
git clone https://github.com/qiuxh016/TravelPilot.git
cd TravelPilot

python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -e ".[dev]"

pnpm install
```

### 环境变量

复制模板：

```powershell
Copy-Item .env.example .env
```

根目录 `.env`：

```env
DATABASE_URL=postgresql+asyncpg://travelpilot:travelpilot@localhost:5432/travelpilot
REDIS_URL=redis://localhost:6379/0
NEXT_PUBLIC_API_URL=http://localhost:8000
```

前端可以创建 `apps/web/.env.local`：

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

不要提交包含真实 API Key 的 `.env` 文件。

### 启动基础服务

```powershell
docker compose up -d postgres redis
docker compose ps
.\\.venv\\Scripts\\alembic.exe upgrade head
```

本地连接：

```text
PostgreSQL: localhost:5432
用户：travelpilot
密码：travelpilot
数据库：travelpilot
Redis: redis://localhost:6379/0
```

### 启动后端

```powershell
uvicorn apps.api.src.travelpilot_api.main:app --reload
```

地址：

```text
http://localhost:8000/health
http://localhost:8000/docs
```

### 启动前端

另开 PowerShell：

```powershell
pnpm --dir apps/web dev
```

默认地址：

```text
http://localhost:3000
```

如果 3000 被占用，Next.js 会自动使用 3001；此时要在 FastAPI CORS 中允许对应端口。

## 当前 API

```text
GET    /health

POST   /api/v1/trips
GET    /api/v1/trips
GET    /api/v1/trips/{trip_id}
PATCH  /api/v1/trips/{trip_id}
DELETE /api/v1/trips/{trip_id}

POST   /api/v1/trips/{trip_id}/wishlist
GET    /api/v1/trips/{trip_id}/wishlist
PATCH  /api/v1/wishlist/{item_id}
DELETE /api/v1/wishlist/{item_id}
```

创建 Trip 示例：

```json
{
  "destination": "成都",
  "start_date": "2026-10-01",
  "end_date": "2026-10-04",
  "budget": 6000,
  "pace_level": 2
}
```

## 测试和质量检查

```powershell
.\\.venv\\Scripts\\python.exe -m compileall -q apps packages
.\\.venv\\Scripts\\python.exe -m pytest -q
.\\.venv\\Scripts\\ruff.exe check .
pnpm --dir apps/web build
```

Playwright：

```powershell
pnpm --dir apps/web exec playwright install chromium
pnpm --dir apps/web exec playwright test
```

核心验收流程：

```text
打开 /trips → 创建旅行 → 进入详情页
→ 添加 Wishlist → 刷新页面 → 确认地点仍然存在
```

## 常用命令

```powershell
docker compose ps
docker compose logs --tail 50 postgres redis

git status --short --branch
git pull
git add .
git commit -m "describe your change"
git push origin main
```

## 当前开发阶段

当前正在完成：

```text
Trip CRUD
  → POI / Wishlist
  → 前后端联调
  → 基础测试和错误处理
```

后续计划：

1. 接入地图 Provider；
2. 增加 POI 搜索和路线查询；
3. 实现确定性基础规划器和 Plan Validator；
4. 接入 Content Agent、MCP Tool Layer；
5. 实现天气、疲劳和取消地点触发的局部重规划；
6. 建立 Traveler Memory 和离线评测集。

