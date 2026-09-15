# TravelPilot Week 1 实施计划

> 目标：用一周时间完成第一个可运行的业务闭环：**创建旅行 → 添加 Wishlist 地点 → 查看旅行和地点**。
>
> 适用环境：Windows、PowerShell、VS Code、Docker Desktop、Python 3.12+、Node.js 20+。
>
> 本周暂不实现：Agent、MCP、截图 OCR、自动爬取、天气智能规划、动态重规划。

---

## 1. 本周结束时应该得到什么

浏览器中可以完成以下流程：

```text
打开前端
  ↓
创建一次旅行，例如“成都 4 日游”
  ↓
后端保存到 PostgreSQL
  ↓
在旅行详情页添加地点
  ↓
后端保存 Wishlist
  ↓
刷新页面后数据仍然存在
```

本周验收标准：

- `GET /health` 返回 `status=ok`；
- PostgreSQL 和 Redis 可以通过 Docker 启动；
- `POST /api/v1/trips` 可以创建旅行；
- `GET /api/v1/trips` 可以返回旅行列表；
- `GET /api/v1/trips/{trip_id}` 可以返回旅行详情；
- 可以为旅行新增、查看、删除 Wishlist 地点；
- Web 页面可以创建旅行并显示列表；
- API 至少有 Trip 和 Wishlist 的自动化测试；
- 所有修改提交到 Git，并推送到 `origin/main`。

---

## 2. 每天使用的软件和语言

### 2.1 软件

| 软件 | 用途 |
|---|---|
| VS Code | 编写 Python、TypeScript、SQL 和 Markdown |
| PowerShell | 执行安装、启动、测试和 Git 命令 |
| Docker Desktop | 启动 PostgreSQL、pgvector 和 Redis |
| 浏览器 | 查看 Web 页面和 FastAPI `/docs` |
| Git | 提交和推送代码 |
| GitHub | 查看远程代码和提交记录 |
| Postman/Insomnia（可选） | 手动测试 HTTP API |

### 2.2 使用的语言

```text
Python       后端 API、领域模型、数据库访问
TypeScript   Next.js 前端
SQL          数据表和查询
Markdown     文档和开发记录
PowerShell   本地命令
```

### 2.3 推荐 VS Code 扩展

在 VS Code 左侧 Extensions 中安装：

- Python（Microsoft）
- Pylance（Microsoft）
- Python Debugger（Microsoft）
- ESLint
- Prettier - Code formatter
- Docker
- PostgreSQL（任选一个可靠扩展）
- GitLens（可选）

打开项目：

```powershell
code D:\codex\TravelPilot
```

以后所有代码都在这个目录中编写。

---

## 3. 第 0 步：确认环境

在 VS Code 中打开 Terminal，终端类型选择 PowerShell，执行：

```powershell
Set-Location D:\codex\TravelPilot
python --version
node --version
pnpm --version
docker --version
docker compose version
git --version
```

建议版本：

```text
Python 3.12+
Node.js 20+
pnpm 9+
Docker Desktop 最新稳定版
Git 2.40+
```

如果没有 pnpm：

```powershell
corepack enable
corepack prepare pnpm@latest --activate
```

如果 Docker 命令失败，先打开 Docker Desktop，等待左下角显示 Docker Engine 正常运行。

检查 Git 分支：

```powershell
git status --short --branch
git branch -vv
```

预期看到：

```text
## main...origin/main
```

---

## 4. 第一天：启动现有项目并理解请求链路

### 4.1 创建 Python 虚拟环境

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 禁止脚本执行，执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

终端前面出现 `(.venv)` 表示成功。

### 4.2 安装 Python 依赖

```powershell
pip install -e ".[dev]"
```

该命令读取根目录的：

```text
pyproject.toml
```

安装：

- FastAPI：后端 Web 框架；
- Uvicorn：启动 API；
- Pydantic：请求和响应校验；
- Pytest：自动化测试；
- HTTPX：测试 API；
- Ruff：Python 检查工具。

### 4.3 启动 API

```powershell
uvicorn apps.api.src.travelpilot_api.main:app --reload
```

打开浏览器访问：

```text
http://localhost:8000/health
http://localhost:8000/docs
```

在 `/docs` 页面点击 `GET /health`，点击 `Try it out`，再点击 `Execute`。

预期结果：

```json
{
  "status": "ok",
  "service": "travelpilot-api"
}
```

### 4.4 认识当前后端入口

打开：

```text
apps/api/src/travelpilot_api/main.py
```

当前结构可以理解为：

```python
app = FastAPI(...)

@app.get("/health")
async def health():
    ...
```

以后新增 API 时，推荐将路由拆到：

```text
apps/api/src/travelpilot_api/api/v1/trips.py
apps/api/src/travelpilot_api/api/v1/wishlist.py
```

不要把所有 API 都继续堆到 `main.py`。

### 4.5 运行已有测试

打开另一个 PowerShell 窗口，进入项目并激活环境：

```powershell
Set-Location D:\codex\TravelPilot
.\.venv\Scripts\Activate.ps1
pytest
```

预期：健康检查测试通过。

### 4.6 第一天验收

完成以下检查后再进入第二天：

- API 可以启动；
- `/health` 可以访问；
- `/docs` 可以打开；
- `pytest` 通过；
- 能在 VS Code 中定位 `apps/api`、`apps/web` 和 `packages/core`。

---

## 5. 第二天：启动数据库和建立数据层

### 5.1 启动 PostgreSQL 和 Redis

在项目根目录执行：

```powershell
docker compose up -d postgres redis
```

查看容器：

```powershell
docker compose ps
```

预期看到两个服务处于 `running` 或 `healthy` 状态：

```text
travelpilot-postgres
travelpilot-redis
```

查看 PostgreSQL 日志：

```powershell
docker compose logs postgres --tail 50
```

### 5.2 连接信息

当前本地数据库连接：

```text
主机：localhost
端口：5432
用户：travelpilot
密码：travelpilot
数据库：travelpilot
```

Redis：

```text
redis://localhost:6379/0
```

这些配置写在根目录：

```text
.env
.env.example
```

不要把真实密码、地图 API Key 或 LLM API Key 提交到 Git。

### 5.3 添加数据库依赖

在已经激活的 `.venv` 中执行：

```powershell
pip install "sqlalchemy[asyncio]" alembic asyncpg redis
```

然后把这些依赖补充到根目录 `pyproject.toml` 的 `dependencies` 中：

```toml
"sqlalchemy[asyncio]>=2.0,<3.0",
"alembic>=1.14,<2.0",
"asyncpg>=0.30,<1.0",
"redis>=5.2,<6.0",
```

原因：命令行临时安装只对当前虚拟环境有效，写进 `pyproject.toml` 才能让其他人复现环境。

### 5.4 创建数据库目录

在 VS Code Explorer 中创建：

```text
packages/infrastructure/src/travelpilot_infra/db/
├─ __init__.py
├─ session.py
├─ base.py
├─ models/
│  ├─ __init__.py
│  ├─ trip.py
│  ├─ poi.py
│  └─ wishlist.py
└─ repositories/
   ├─ __init__.py
   ├─ trips.py
   └─ wishlist.py
```

### 5.5 先写数据库连接

文件：

```text
packages/infrastructure/src/travelpilot_infra/db/session.py
```

职责：

- 从环境变量读取 `DATABASE_URL`；
- 创建 SQLAlchemy Async Engine；
- 创建 AsyncSession；
- 给 API 和 Worker 使用。

不要在 API 路由中直接写连接字符串。

### 5.6 创建 Alembic

在项目根目录执行：

```powershell
alembic init packages/infrastructure/src/travelpilot_infra/db/migrations
```

然后修改：

```text
alembic.ini
packages/infrastructure/src/travelpilot_infra/db/migrations/env.py
```

将数据库 URL 改为从 `DATABASE_URL` 环境变量读取。

第一周目标不是把 Alembic 配置做得复杂，而是让下面两条命令可以运行：

```powershell
alembic revision --autogenerate -m "create trip tables"
alembic upgrade head
```

### 5.7 第二天验收

```powershell
docker compose ps
alembic upgrade head
```

确认：

- PostgreSQL 容器正在运行；
- 数据库连接没有报错；
- 数据库中产生了 Alembic 版本表；
- `.env` 没有被 Git 跟踪。

---

## 6. 第三天：实现 Trip 数据模型和 CRUD API

### 6.1 先定义 Pydantic 请求模型

创建目录：

```text
apps/api/src/travelpilot_api/schemas/
```

创建文件：

```text
apps/api/src/travelpilot_api/schemas/trips.py
```

建议定义：

```python
class TripCreate(BaseModel):
    destination: str
    start_date: date
    end_date: date
    budget: Decimal | None = None
    pace_level: int = Field(default=3, ge=1, le=5)


class TripRead(TripCreate):
    id: UUID
    status: str
```

这些类负责检查 HTTP 输入，不负责数据库保存。

### 6.2 定义 SQLAlchemy 模型

文件：

```text
packages/infrastructure/src/travelpilot_infra/db/models/trip.py
```

字段建议：

```text
id
destination
start_date
end_date
budget
pace_level
status
created_at
updated_at
```

状态先使用：

```text
DRAFT
PLANNING
READY
ACTIVE
COMPLETED
```

### 6.3 定义 Repository

文件：

```text
packages/infrastructure/src/travelpilot_infra/db/repositories/trips.py
```

Repository 提供：

```python
create(trip)
get_by_id(trip_id)
list_by_user(user_id)
update(trip_id, changes)
delete(trip_id)
```

Repository 负责 SQL 查询，不负责 HTTP 状态码，也不负责理解用户自然语言。

### 6.4 定义 Application Service

创建：

```text
packages/core/src/travelpilot_core/application/
├─ __init__.py
└─ trips.py
```

里面放：

```text
CreateTrip
ListTrips
GetTrip
UpdateTrip
DeleteTrip
```

这样 API 只是调用用例：

```text
HTTP Request
  → Schema 校验
  → Application Service
  → Repository
  → Database
```

### 6.5 创建路由

创建：

```text
apps/api/src/travelpilot_api/api/v1/trips.py
```

实现：

```text
POST   /api/v1/trips
GET    /api/v1/trips
GET    /api/v1/trips/{trip_id}
PATCH  /api/v1/trips/{trip_id}
DELETE /api/v1/trips/{trip_id}
```

再到 `main.py` 中注册 router。

### 6.6 用 Swagger 测试

启动 API：

```powershell
uvicorn apps.api.src.travelpilot_api.main:app --reload
```

打开：

```text
http://localhost:8000/docs
```

创建一条旅行：

```json
{
  "destination": "成都",
  "start_date": "2026-10-01",
  "end_date": "2026-10-04",
  "budget": 6000,
  "pace_level": 2
}
```

然后使用 `GET /api/v1/trips` 检查是否能查到。

### 6.7 第三天验收

- 创建旅行成功；
- 查询旅行成功；
- 修改旅行成功；
- 删除旅行成功；
- 重启 API 后数据仍然存在；
- 不存在的 Trip 返回 404；
- `pace_level=0` 或 `pace_level=6` 返回 422。

---

## 7. 第四天：实现 POI 和 Wishlist

### 7.1 先区分 POI 和 Wishlist Item

POI 是地点本身：

```text
成都大熊猫繁育研究基地
```

Wishlist Item 是用户在某次旅行中收藏了这个地点：

```text
用户 A 在成都旅行中收藏了熊猫基地
```

所以两者不能合成一张表。

### 7.2 创建 POI 模型

文件：

```text
packages/infrastructure/src/travelpilot_infra/db/models/poi.py
```

字段：

```text
id
name
category
address
latitude
longitude
suggested_duration_min
opening_hours
provider
provider_id
```

第一周先允许用户手动输入 POI，不需要接高德 API。

### 7.3 创建 Wishlist 模型

文件：

```text
packages/infrastructure/src/travelpilot_infra/db/models/wishlist.py
```

字段：

```text
id
trip_id
poi_id
priority
interest_score
notes
source_type
source_url
created_at
```

### 7.4 Wishlist API

实现：

```text
POST   /api/v1/trips/{trip_id}/wishlist
GET    /api/v1/trips/{trip_id}/wishlist
PATCH  /api/v1/wishlist/{item_id}
DELETE /api/v1/wishlist/{item_id}
```

新增请求示例：

```json
{
  "name": "成都大熊猫繁育研究基地",
  "category": "ATTRACTION",
  "address": "成都市成华区",
  "latitude": 30.73,
  "longitude": 104.14,
  "suggested_duration_min": 180,
  "priority": 1,
  "notes": "早上去，避开人流"
}
```

### 7.5 数据库约束

增加以下约束：

- `start_date <= end_date`；
- `pace_level` 在 1 到 5 之间；
- `latitude` 在 -90 到 90 之间；
- `longitude` 在 -180 到 180 之间；
- 同一旅行不能重复收藏完全相同的 POI；
- 删除 Trip 时删除它的 Wishlist 关联。

### 7.6 第四天验收

- 可以给指定 Trip 添加地点；
- 可以查看该 Trip 的地点；
- 可以修改优先级和备注；
- 可以删除地点；
- 不同 Trip 之间的 Wishlist 不会混淆；
- 删除 Trip 后关联 Wishlist 不会残留。

---

## 8. 第五天：连接前端和后端

### 8.1 创建前端 API Client

创建：

```text
apps/web/lib/api.ts
```

先实现：

```typescript
export async function listTrips() {}
export async function createTrip(input: TripCreate) {}
export async function getTrip(id: string) {}
export async function listWishlist(tripId: string) {}
export async function addWishlistItem(tripId: string, input: PoiCreate) {}
```

API 地址从：

```text
NEXT_PUBLIC_API_URL
```

读取，不要把 `http://localhost:8000` 散落在多个组件中。

### 8.2 创建 Trip 列表页

创建：

```text
apps/web/app/trips/page.tsx
```

功能：

- 调用 `GET /api/v1/trips`；
- 显示目的地、日期、状态；
- 点击进入详情页；
- 添加“创建旅行”按钮。

### 8.3 创建 Trip 表单页

创建：

```text
apps/web/app/trips/new/page.tsx
```

表单字段：

```text
目的地
开始日期
结束日期
预算
旅行节奏
```

提交时调用：

```text
POST /api/v1/trips
```

成功后跳转：

```text
/trips/{trip_id}
```

### 8.4 创建 Trip 详情页

创建：

```text
apps/web/app/trips/[tripId]/page.tsx
```

第一版先分三个区域：

```text
旅行基本信息
Wishlist 地点列表
添加地点表单
```

地图先使用占位区域即可，第一周不要为了地图阻塞业务闭环。

### 8.5 前端运行

另开 PowerShell：

```powershell
Set-Location D:\codex\TravelPilot
pnpm install
pnpm --dir apps/web dev
```

打开：

```text
http://localhost:3000
```

### 8.6 第五天验收

用浏览器完成：

```text
打开 /trips
  → 创建成都旅行
  → 自动跳转详情页
  → 添加熊猫基地
  → 刷新页面
  → 地点仍然存在
```

---

## 9. 第六天：补测试、错误处理和开发体验

### 9.1 后端测试

创建：

```text
apps/api/tests/test_trips.py
apps/api/tests/test_wishlist.py
```

至少覆盖：

```text
创建成功
查询成功
查询不存在返回 404
日期错误返回 422
添加 Wishlist 成功
删除 Wishlist 成功
跨 Trip 访问被拒绝或返回 404
```

执行：

```powershell
pytest -q
```

### 9.2 前端测试

第一周可以先准备 Playwright，而不必覆盖全部页面：

```powershell
pnpm --dir apps/web exec playwright install chromium
```

核心 E2E 用例：

```text
打开首页
进入 Trip 列表
创建 Trip
进入详情
添加 Wishlist
刷新页面
确认 Wishlist 仍存在
```

### 9.3 错误处理

后端统一返回：

```json
{
  "error": {
    "code": "TRIP_NOT_FOUND",
    "message": "Trip does not exist"
  }
}
```

前端处理：

- 加载状态；
- 空列表状态；
- 请求失败提示；
- 表单校验错误；
- 提交按钮 loading 状态。

### 9.4 代码质量检查

```powershell
ruff check .
python -m compileall -q apps packages
pnpm --dir apps/web build
```

如果项目暂时没有完整 lint 配置，至少保证编译、测试和构建可以通过。

---

## 10. 第七天：完整验收、提交和推送

### 10.1 启动全部依赖

终端 1：

```powershell
docker compose up -d postgres redis
```

终端 2：

```powershell
.\.venv\Scripts\Activate.ps1
alembic upgrade head
uvicorn apps.api.src.travelpilot_api.main:app --reload
```

终端 3：

```powershell
pnpm --dir apps/web dev
```

### 10.2 手动验收清单

按下面顺序操作：

1. 打开 `http://localhost:3000`；
2. 进入旅行列表；
3. 创建一条成都旅行；
4. 输入 2026-10-01 到 2026-10-04；
5. 设置预算 6000；
6. 设置节奏为 2；
7. 进入旅行详情；
8. 添加熊猫基地；
9. 添加宽窄巷子；
10. 修改一个地点的备注；
11. 删除一个地点；
12. 刷新页面；
13. 确认数据仍然存在；
14. 直接打开 `/docs` 检查 API；
15. 执行 `pytest -q`。

### 10.3 Git 提交

确认没有敏感文件：

```powershell
git status --short
git diff -- .env
```

提交：

```powershell
git add .
git commit -m "feat: implement trip and wishlist foundation"
git push origin main
```

确认远程状态：

```powershell
git status --short --branch
git log --oneline --decorate -3
```

预期类似：

```text
## main...origin/main
```

### 10.4 写开发周报

在项目根目录新建：

```text
docs/weekly/week1-summary.md
```

记录：

```text
本周完成了什么
哪些接口已经可用
哪些测试已通过
遇到了什么问题
下周准备实现什么
```

---

## 11. 推荐的实际编码顺序

每写一个功能，都按以下顺序进行：

```text
1. 先写数据结构
2. 再写数据库模型
3. 再写 Repository
4. 再写 Application Service
5. 再写 API 路由
6. 用 Swagger 手动测试
7. 写自动化测试
8. 再接前端
9. 浏览器手动验收
10. Git commit
```

不要采用以下顺序：

```text
先写复杂页面
再猜后端接口
最后才考虑数据库
```

因为这会导致前后端数据结构不断反复修改。

---

## 12. 第一周结束后，第二周做什么

如果第一周闭环稳定，第二周进入：

```text
POI Provider 接入
  ↓
高德地点搜索
  ↓
高德地点详情
  ↓
路线和距离矩阵
  ↓
地图展示
  ↓
确定性基础规划
```

第二周的目标是：

> 不使用 Agent，也能根据真实 POI 坐标和路线距离生成一份基础日程。

之后再进入 Agent 和 MCP，避免一开始只得到“会聊天但不能执行”的原型。

