# TravelPilot 项目结构与开源参考

> 文档状态：Draft v1  
> 调研日期：2026-09-15  
> 适用阶段：MVP / 单团队开发  
> 关联文档：[TravelPilot 项目初步规划](./TravelPilot_项目规划.md)

## 1. 文档目标

本文将产品规划进一步落实为可执行的工程方案，解决以下问题：

1. 哪些开源项目值得 TravelPilot 学习；
2. 每个项目具体学习什么，而不是直接照搬什么；
3. MVP 应采用怎样的仓库、模块和依赖结构；
4. 未来规模扩大时，哪些模块可以自然拆分。

本文只将外部项目视为设计参考。引入源码、复制代码或分发修改版本前，仍需单独核对目标版本的许可证和 NOTICE 要求。

---

## 2. 总体架构结论

MVP 推荐采用：

> **Monorepo + Modular Monolith API + Independent Worker + Provider Adapters**

即：

- 一个 Monorepo 管理 Web、API、Worker、共享契约和部署配置；
- 后端先做模块化单体，不按 Agent 或领域拆微服务；
- API 负责同步请求、权限和任务提交；
- Worker 负责内容解析、信息补全、规划、验证、重规划等长任务；
- PostgreSQL 是业务事实的唯一可信来源；
- Redis 只承担缓存、队列和短期状态，不保存核心业务事实；
- LLM、地图、天气、搜索全部通过 Port/Adapter 隔离；
- Agent 负责语义理解和工具选择，确定性引擎负责计算与最终可行性判断。

### 2.1 逻辑视图

```text
Web / PWA
    │ REST + SSE
    ▼
FastAPI API ─────────────── PostgreSQL + pgvector
    │                           ▲
    │ enqueue                   │ persist/checkpoint
    ▼                           │
Redis Queue ───────► Worker / Workflow Runtime
                         │
             ┌───────────┼────────────┐
             ▼           ▼            ▼
          Agents     Deterministic   Provider Ports
                       Engines            │
                         │       ┌────────┼────────┐
                         ▼       ▼        ▼        ▼
                      OR-Tools  Maps   Weather   Search/LLM
```

### 2.2 为什么暂不拆微服务

Trip、Wishlist、Plan、Event 和 Memory 的事务边界尚在快速变化。此时拆微服务会提前引入分布式事务、接口版本、链路追踪、部署和本地调试成本。模块化单体仍能通过清晰依赖边界为以后拆分做准备。

建议到以下信号出现后再拆服务：

- 内容解析或规划任务需要独立扩缩容；
- 某模块有独立团队和发布节奏；
- 单体部署成为明确的可靠性或性能瓶颈；
- Provider Gateway 需要服务于多个产品。

---

## 3. 开源项目参考清单

### 3.1 第一优先级：直接影响 MVP 架构

| 项目 | 可学习的模块/思想 | TravelPilot 中的落点 | 采用建议 |
|---|---|---|---|
| [FastAPI Full Stack Template](https://github.com/fastapi/full-stack-fastapi-template) | 前后端分区、配置、数据库迁移、Docker Compose、测试和 CI 基线 | `apps/api`、`apps/web`、`infra/compose`、CI | 学工程骨架；前端仍按本项目选用 Next.js，不必照搬其全部技术选择 |
| [OpenTripPlanner](https://github.com/opentripplanner/OpenTripPlanner) / [Architecture](https://github.com/opentripplanner/OpenTripPlanner/blob/dev-2.x/ARCHITECTURE.md) | Use Case Service、领域模型隔离、路由核心与外层模型映射、候选行程过滤链、架构决策记录 | `application`、`domain/planning`、`itinerary_ranker`、`docs/adr` | 深度学习领域拆分和后处理管线；不引入其 Java 代码或完整交通路由栈 |
| [Google OR-Tools](https://github.com/google/or-tools) | CP-SAT、VRP/TSP、时间窗、容量/成本约束、解状态 | `engines/optimizer` | MVP 的确定性规划核心；先封装成 Python Port，避免求解器类型渗透到领域层 |
| [PostGIS](https://github.com/postgis/postgis) | 地理类型、距离查询、空间索引、范围检索 | `pois`、`geo`、数据库 migration | 用于 POI 坐标、附近搜索和粗筛；真实路程仍由地图 Provider 返回 |
| [pgvector](https://github.com/pgvector/pgvector) | 向量与关系数据共库、精确/近似检索、元数据过滤 | `memory`、内容去重、攻略召回 | V1 使用 PostgreSQL 扩展即可，不单独部署向量数据库 |
| [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk) | Tool schema、Client/Server、传输层与能力解耦 | `integrations/mcp` | 将 MCP 作为一种适配器，不让领域服务依赖 MCP SDK |

### 3.2 Agent、工作流与可观测性

| 项目 | 可学习的模块/思想 | TravelPilot 中的落点 | 采用建议 |
|---|---|---|---|
| [LangGraph](https://github.com/langchain-ai/langgraph) | 显式状态图、checkpoint、interrupt/resume、human-in-the-loop | `workflows`、`workflow_runs` | 先学习状态与恢复模型；MVP 可自建有限状态工作流，复杂度上升后再评估引入 |
| [Temporal Python SDK](https://github.com/temporalio/sdk-python) / [Samples](https://github.com/temporalio/samples-python) | Durable execution、Activity、重试、超时、幂等和确定性重放 | 长任务可靠执行、人工确认后的恢复 | 不建议作为第一周依赖；当任务跨分钟/小时、恢复要求明确时再引入 |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM Trace、Prompt、Dataset、Evaluation、成本与延迟观测 | `observability`、`evaluation` | 优先通过 OpenTelemetry/SDK 接入；业务 run 仍保存在自有数据库 |
| [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | 统一 Trace/Metric/Log 语义与上下文传播 | API、Worker、Provider 调用链 | 建议早期预留 `trace_id/run_id`，基础闭环稳定后接入 Collector |
| [Pydantic](https://github.com/pydantic/pydantic) | 数据验证、判别联合、JSON Schema、结构化输出契约 | API DTO、Agent 输出、Tool 输入输出 | 可以直接采用；领域实体不要全部退化成传输 DTO |

### 3.3 前端地图与交互

| 项目 | 可学习的模块/思想 | TravelPilot 中的落点 | 采用建议 |
|---|---|---|---|
| [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) | 地图图层、Source/Layer、Marker、线路可视化 | `features/map` | 可学习地图状态组织；国内底图、坐标系和服务条款需按实际 Provider 单独确认 |
| [react-map-gl](https://github.com/visgl/react-map-gl) | React 地图封装、受控视口、图层组件化 | Web 地图组件 | 若使用兼容 MapLibre 的方案可采用；Provider 专有能力放入 Adapter |
| [TanStack Query](https://github.com/TanStack/query) | Server State、缓存、失效、Mutation、乐观更新 | Web 的 API 数据访问层 | 推荐采用；不要把服务端状态重复塞进全局 UI Store |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | 可复制组件、可维护的本地 UI 源码、组合模式 | `components/ui` | 适合快速搭建 Workspace；业务组件应放在 `features/*`，不要都堆进 `components` |
| [dnd-kit](https://github.com/clauderic/dnd-kit) | 可访问拖拽、排序、传感器模型 | Timeline 节点调整 | 在 Timeline 手动调整阶段引入，不作为第一批基础依赖 |

### 3.4 旅行产品与研究型参考

| 项目 | 可学习的模块/思想 | TravelPilot 中的落点 | 注意事项 |
|---|---|---|---|
| [ITINERA](https://github.com/YihongT/ITINERA) / [论文](https://arxiv.org/abs/2402.07204) | 自然语言意图与空间优化结合、城市行程规划评测 | Planner Pipeline、Evaluation Dataset | 适合作为算法与评测参考，不直接作为生产骨架 |
| [OpenTripPlanner](https://www.opentripplanner.org/) | 多模态路径、候选 itinerary、请求/响应映射 | Route Provider、候选计划过滤 | 其目标是公共交通 journey planning，与多日旅游行程并不等价 |
| [trip-planner](https://github.com/natcat38/trip-planner) | 日程、地图同步、多人协作、离线 PWA 的产品组织 | Trip Workspace、Map/Timeline 联动 | 可参考产品交互；采用任何代码前检查当时许可证与成熟度 |
| [travel-itinerary-app](https://github.com/maneeshmkp/travel-itinerary-app) | FastAPI + Next.js 的旅行数据 CRUD、MCP 示例 | API 原型、种子数据和 Demo 思路 | 规模较小，只适合做实现对照，不作为架构权威 |

### 3.5 明确不建议直接照搬的部分

- 不将每个 Agent 部署为一个服务；Agent 是应用层能力，不天然是部署边界。
- 不让 LangGraph、Temporal 或任一 Agent 框架的数据结构成为核心 Trip/Plan 模型。
- 不将地图 Provider 返回结构直接存为领域模型；先映射为内部统一类型，并保留原始响应引用。
- 不用向量数据库替代关系模型；Trip、Plan、Reservation 等需要事务和强约束。
- 不让 LLM 直接生成最终可执行计划；输出必须通过约束验证器。
- 不在 MVP 自建完整道路图路由引擎；国内场景优先使用高德等 Provider，通过 Port 隔离。

---

## 4. 推荐仓库结构

```text
TravelPilot/
├─ apps/
│  ├─ web/                         # Next.js 用户端/PWA
│  │  ├─ src/
│  │  │  ├─ app/                  # 路由、layout、页面入口
│  │  │  ├─ features/             # 按业务能力组织
│  │  │  │  ├─ trips/
│  │  │  │  ├─ wishlist/
│  │  │  │  ├─ itinerary/
│  │  │  │  ├─ plan-check/
│  │  │  │  ├─ replanning/
│  │  │  │  ├─ traveler-profile/
│  │  │  │  └─ map/
│  │  │  ├─ components/
│  │  │  │  ├─ ui/                # 通用原子组件
│  │  │  │  └─ layout/            # 页面骨架
│  │  │  ├─ lib/                  # API client、query client、工具函数
│  │  │  ├─ hooks/
│  │  │  ├─ stores/               # 仅 UI/临时交互状态
│  │  │  └─ styles/
│  │  └─ tests/
│  │
│  ├─ api/                         # FastAPI 同步入口
│  │  ├─ src/travelpilot/
│  │  │  ├─ main.py
│  │  │  ├─ api/                  # HTTP transport，不写业务规则
│  │  │  │  ├─ deps.py
│  │  │  │  ├─ errors.py
│  │  │  │  └─ v1/
│  │  │  │     ├─ trips.py
│  │  │  │     ├─ wishlist.py
│  │  │  │     ├─ plans.py
│  │  │  │     ├─ events.py
│  │  │  │     └─ runs.py
│  │  │  └─ bootstrap/            # 配置、依赖组装、生命周期
│  │  └─ tests/
│  │
│  └─ worker/                      # 异步任务进程
│     ├─ src/travelpilot_worker/
│     │  ├─ main.py
│     │  ├─ tasks/                 # 队列任务入口
│     │  └─ schedules/             # 未来的周期任务
│     └─ tests/
│
├─ packages/
│  ├─ core/                        # Python 业务核心
│  │  └─ src/travelpilot_core/
│  │     ├─ domain/               # 纯业务模型与规则
│  │     │  ├─ trips/
│  │     │  ├─ wishlist/
│  │     │  ├─ planning/
│  │     │  ├─ events/
│  │     │  └─ memory/
│  │     ├─ application/          # 用例编排与事务边界
│  │     │  ├─ commands/
│  │     │  ├─ queries/
│  │     │  ├─ services/
│  │     │  └─ dto/
│  │     ├─ agents/               # 语义判断/工具选择
│  │     │  ├─ content/
│  │     │  ├─ research/
│  │     │  ├─ planner/
│  │     │  ├─ validator/
│  │     │  └─ replanner/
│  │     ├─ workflows/            # 显式状态、步骤、恢复点
│  │     │  ├─ ingestion/
│  │     │  ├─ planning/
│  │     │  └─ replanning/
│  │     ├─ engines/              # 确定性计算
│  │     │  ├─ constraints/
│  │     │  ├─ optimizer/
│  │     │  ├─ geo/
│  │     │  ├─ scoring/
│  │     │  └─ impact_analysis/
│  │     └─ ports/                # Repository/Provider/Queue/LLM 接口
│  │        ├─ repositories.py
│  │        ├─ maps.py
│  │        ├─ weather.py
│  │        ├─ search.py
│  │        ├─ llm.py
│  │        ├─ queue.py
│  │        └─ clock.py
│  │
│  ├─ infrastructure/             # Python 技术实现
│  │  └─ src/travelpilot_infra/
│  │     ├─ db/
│  │     │  ├─ models/
│  │     │  ├─ repositories/
│  │     │  ├─ migrations/
│  │     │  └─ unit_of_work.py
│  │     ├─ providers/
│  │     │  ├─ maps/amap/
│  │     │  ├─ weather/
│  │     │  ├─ search/
│  │     │  └─ llm/
│  │     ├─ mcp/                  # MCP client/server adapters
│  │     ├─ queue/
│  │     ├─ cache/
│  │     └─ observability/
│  │
│  ├─ contracts/                  # OpenAPI/schema 与生成代码配置
│  │  ├─ openapi/
│  │  └─ events/
│  │
│  └─ config/                     # 共享 lint/type/test 配置
│
├─ tests/
│  ├─ contract/                   # Provider/API 契约测试
│  ├─ integration/                # DB、Redis、队列、外部适配器
│  ├─ e2e/                        # Playwright 核心用户闭环
│  ├─ evaluation/                 # 规划/重规划离线评测
│  │  ├─ cases/
│  │  ├─ datasets/
│  │  └─ metrics/
│  └─ fixtures/
│
├─ docs/
│  ├─ architecture/
│  ├─ adr/                        # Architecture Decision Records
│  ├─ api/
│  ├─ domain/
│  ├─ runbooks/
│  └─ research/
│
├─ infra/
│  ├─ compose/
│  ├─ docker/
│  ├─ migrations/
│  └─ observability/
│
├─ scripts/                       # 本地开发、seed、评测入口
├─ .github/workflows/
├─ pyproject.toml                 # Python workspace、lint、test
├─ pnpm-workspace.yaml
├─ package.json
├─ docker-compose.yml
├─ .env.example
├─ Makefile                       # 或 Windows 友好的 task runner 配置
└─ README.md
```

### 4.1 关于 Monorepo 工具

MVP 不必为了“像 Monorepo”立即引入复杂构建系统：

- TypeScript 包管理：`pnpm workspace`；
- Python 包管理：`uv workspace` 或单一 `pyproject.toml`；
- 前端只有一个应用时，暂不需要 Turborepo；
- 当 Web、Admin、Docs 多应用出现并需要共享缓存时再引入 Turborepo/Nx。

---

## 5. 后端模块边界

### 5.1 Domain：只表达旅行领域

领域层包含：

- Entity：`Trip`、`WishlistItem`、`Plan`、`PlanNode`、`TripEvent`、`TravelerProfile`；
- Value Object：`TimeWindow`、`GeoPoint`、`Money`、`Duration`、`Constraint`；
- Domain Service：计划变更成本、节点可行性、事件影响传播；
- Domain Event：`TripCreated`、`PlanGenerated`、`TripEventReceived`、`PlanPatched`。

领域层禁止依赖：

- FastAPI；
- SQLAlchemy；
- Redis；
- 地图/天气厂商 SDK；
- MCP SDK；
- 具体 LLM SDK；
- Agent 框架。

### 5.2 Application：一个用例一个入口

建议以用例而不是数据库表组织服务，例如：

```text
CreateTrip
ImportContent
ResolvePoiCandidates
GeneratePlan
ValidatePlan
ApplyTripEvent
ReplanAffectedWindow
RecordTripFeedback
```

Application 层负责：

- 读取/保存聚合；
- 调用 Agent、Engine 和 Port；
- 定义事务边界；
- 提交异步任务；
- 记录业务 Run；
- 不承载 HTTP、SQL 或 Provider 细节。

### 5.3 Agent 与 Engine 的责任边界

| 问题 | Agent | Engine |
|---|---:|---:|
| 理解“不想太累” | 是 | 否 |
| 抽取候选 POI | 是 | 否 |
| 决定调用哪个信息源 | 是 | 否 |
| 验证营业时间是否冲突 | 可解释 | 是 |
| 计算路线时长 | 发起调用 | Provider/Engine |
| 满足时间窗和预算约束 | 提供偏好 | 是 |
| 给候选计划写自然语言说明 | 是 | 否 |
| 决定最终计划是否 feasible | 否 | 是 |

原则：

> Agent 可以提出候选和解释结果，但不可绕过 Engine 将计划标记为可执行。

### 5.4 Port/Adapter 示例

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RouteRequest:
    origin: tuple[float, float]
    destination: tuple[float, float]
    departure_at: datetime
    mode: str


@dataclass(frozen=True)
class RouteResult:
    duration_seconds: int
    distance_meters: int
    provider: str
    fetched_at: datetime


class MapsPort(Protocol):
    async def get_route(self, request: RouteRequest) -> RouteResult: ...
```

高德响应字段、签名、限流和坐标转换都留在 `infrastructure/providers/maps/amap` 中。

---

## 6. 核心数据模型建议

### 6.1 Plan 必须版本化

不要直接覆盖当前行程。建议：

```text
trip_plans
  id
  trip_id
  version
  parent_plan_id
  status             # CANDIDATE / VALID / ACTIVE / SUPERSEDED
  trigger_type       # INITIAL / USER_EDIT / EVENT_REPLAN
  created_by_run_id
  created_at

trip_plan_nodes
  id
  plan_id
  stable_key         # 跨版本识别同一逻辑节点
  ...

plan_changes
  from_plan_id
  to_plan_id
  node_stable_key
  operation          # ADD / REMOVE / MOVE / UPDATE / KEEP
  reason_code
  change_cost
```

这样 Plan Diff、回滚、评测和“最小扰动”都有事实基础。

### 6.2 区分来源事实、推断和用户确认

POI 营业时间、推荐时长等字段建议附带 provenance：

```text
value
source_type          # PROVIDER / WEB / USER / MODEL
source_ref
observed_at
confidence
expires_at
verified_by_user
```

不要把模型推断的 `opening_hours` 与地图接口返回值视为同等可信。

### 6.3 Run 模型统一

建议统一以下标识：

```text
request_id     # 一次 HTTP 请求
workflow_id    # 一条可恢复业务流程
run_id         # 一次 Agent/Engine 执行
trace_id       # 可观测链路
trip_id        # 业务聚合
plan_id        # 计划版本
```

`agent_runs` 和 `tool_calls` 可先保留业务必要摘要；大体积 Prompt/Response、Trace 和评测细节交给可观测平台或对象存储，并做隐私脱敏。

---

## 7. 工作流设计

### 7.1 内容导入

```text
RECEIVED
  → PARSED
  → ENTITIES_EXTRACTED
  → POI_CANDIDATES_RESOLVED
  → USER_CONFIRMATION_REQUIRED（可选）
  → ENRICHED
  → IMPORTED
```

每一步应满足：

- 输入输出有 schema；
- 可重试；
- 外部副作用有幂等键；
- 失败状态可见；
- 需要用户判断时可暂停，而不是循环询问模型。

### 7.2 初始规划

```text
NORMALIZE_CONTEXT
  → BUILD_ROUTE_MATRIX
  → GENERATE_CANDIDATES
  → SOLVE_CONSTRAINTS
  → VALIDATE
  → RANK
  → EXPLAIN
  → PERSIST_PLAN_VERSION
```

### 7.3 局部重规划

```text
RECEIVE_EVENT
  → DETECT_AFFECTED_NODES
  → EXPAND_LOCAL_WINDOW
  → FREEZE_UNAFFECTED_NODES
  → GENERATE_PATCH_CANDIDATES
  → SOLVE_AND_VALIDATE
  → CALCULATE_CHANGE_COST
  → PERSIST_NEW_VERSION_AND_DIFF
```

第一版只需支持当前时刻到当天结束的局部窗口，比通用 DAG 增量求解更容易验证。

---

## 8. API 与前端状态规划

### 8.1 API 风格

CRUD 保持 REST，长任务使用 Job Resource：

```text
POST /api/v1/trips
POST /api/v1/trips/{trip_id}/imports
POST /api/v1/trips/{trip_id}/plan-runs
POST /api/v1/trips/{trip_id}/validation-runs
POST /api/v1/trips/{trip_id}/events
POST /api/v1/trips/{trip_id}/replan-runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events       # SSE
GET  /api/v1/trips/{trip_id}/plans/{id}/diff
```

推荐 SSE 传递进度，不要一开始就为单向状态更新引入 WebSocket。

### 8.2 前端边界

- `features/trips`：旅行基本信息和状态；
- `features/wishlist`：收藏导入、候选确认和优先级；
- `features/map`：地图展示与 Provider 封装；
- `features/itinerary`：Timeline 和 Plan Version；
- `features/plan-check`：冲突与风险；
- `features/replanning`：事件输入和 Diff；
- `features/traveler-profile`：显式偏好和记忆确认。

状态原则：

- TanStack Query 管服务端状态；
- URL 管页面可分享状态，如当前 day、选中 node；
- 本地 Store 只管拖拽中间态、面板开关等 UI 状态；
- 拖拽后先生成 Plan Patch，再由后端验证，不直接改“已验证计划”。

---

## 9. 测试与评估结构

### 9.1 测试金字塔

```text
Domain unit tests
    ↑ 数量最多，纯函数、快
Application tests
    ↑ 使用 fake ports，验证用例和事务
Adapter contract tests
    ↑ 对地图/天气/LLM 的录制响应与 schema
Integration tests
    ↑ PostgreSQL、Redis、Worker
E2E tests
    ↑ 只覆盖关键闭环
Offline evaluation
    ↑ 规划质量、约束违反率、最小扰动
```

### 9.2 必须建立的固定样例

建议先创建成都样例包：

```text
tests/evaluation/cases/chengdu_4d_relaxed/
├─ trip.json
├─ wishlist.json
├─ provider_snapshots/
├─ expected_constraints.json
├─ events/
│  ├─ heavy_rain.json
│  ├─ fatigue.json
│  └─ poi_closed.json
└─ assertions.yaml
```

初期至少自动验证：

- Hard Constraint Violation = 0；
- Opening Hours Conflict = 0；
- 固定交通/预约节点不移动；
- 每日 POI 数符合 pace 范围；
- Replan 只修改局部窗口；
- 每个变更都有机器可读 reason code；
- Provider 失败时不会生成未经验证的“可执行”结论。

---

## 10. 依赖与工程规则

### 10.1 依赖方向

```text
API / Worker
    ↓
Application
    ↓
Domain + Ports
    ↑
Infrastructure Adapters
```

允许：Infrastructure 实现 Core 定义的 Port。  
禁止：Core import Infrastructure；Domain import FastAPI/SQLAlchemy/Provider SDK。

### 10.2 推荐基础依赖

后端首批：

```text
fastapi
uvicorn
pydantic / pydantic-settings
sqlalchemy
alembic
asyncpg
redis
httpx
or-tools
pytest / pytest-asyncio
ruff
mypy 或 pyright（二选一）
```

前端首批：

```text
next / react / typescript
tailwindcss
@tanstack/react-query
zod
地图 SDK 或其 React adapter
vitest
playwright
```

暂缓引入：

- Kubernetes；
- Kafka；
- 独立向量数据库；
- Service Mesh；
- 多个 Agent 框架；
- 同时存在 Celery、Temporal、LangGraph 三套执行模型。

### 10.3 配置规则

- 配置通过环境变量和 `pydantic-settings` 注入；
- `.env.example` 只放变量名和无敏感默认值；
- Provider Key 不进入浏览器 bundle；
- 每个外部 Provider 配置 timeout、retry、rate limit 和 circuit breaker；
- 日期时间统一存 UTC，另存 IANA 时区；旅行页面按目的地时区展示；
- 坐标明确标记坐标系，国内地图尤其不可只存裸 `lat/lng`。

---

## 11. 分阶段落地方案

### Phase A：工程骨架与无 AI 闭环

创建：

- `apps/web`、`apps/api`、`apps/worker`；
- `packages/core`、`packages/infrastructure`；
- PostgreSQL、Redis、migration、CI；
- Trip/Wishlist CRUD；
- 地图搜索 Adapter；
- 一条 API + Worker + DB 的端到端测试。

验收：用户能创建旅行、搜索地点、加入 Wishlist。

### Phase B：确定性规划最小内核

创建：

- `domain/planning`；
- `engines/constraints`；
- `engines/optimizer`；
- Route Matrix Port；
- 版本化 Plan 和 Validator。

验收：固定样例能生成 Hard Constraint 为零的计划。

### Phase C：内容理解和 Agent

创建：

- Content Agent；
- Research Agent；
- Structured Output schema；
- provenance/confidence；
- Tool Trace。

验收：文本、截图、URL 至少三种输入进入同一 Wishlist 流程，歧义 POI 可由用户确认。

### Phase D：局部重规划

创建：

- Event Schema；
- Impact Analysis；
- Frozen Node / Local Window；
- Plan Version / Plan Diff；
- 天气、疲劳、取消 POI 三类事件。

验收：重规划无 Hard Constraint，且 Changed Node Count 明显少于全量重排。

### Phase E：记忆、评测与可观测性

创建：

- Traveler Profile；
- Candidate Memory 和确认机制；
- 离线 Dataset；
- Trace、Token、Latency、Tool Success 指标；
- 回归评测流水线。

验收：规划策略或 Prompt 修改后可自动比较质量与成本变化。

---

## 12. 建议立即建立的 ADR

在 `docs/adr` 中建立以下决策记录：

1. `0001-modular-monolith.md`：MVP 使用模块化单体；
2. `0002-plan-versioning.md`：计划不可覆盖，只能生成新版本；
3. `0003-provider-port-adapter.md`：地图/天气/搜索统一 Port；
4. `0004-agent-engine-boundary.md`：LLM 不负责最终可行性；
5. `0005-postgres-source-of-truth.md`：核心状态只以 PostgreSQL 为准；
6. `0006-background-job-model.md`：异步执行框架的初始选择；
7. `0007-coordinate-and-timezone.md`：坐标系与时区规范；
8. `0008-sensitive-data-retention.md`：截图、订单、位置和 Prompt 的留存策略。

---

## 13. 最终推荐

TravelPilot 第一版的竞争力不来自堆叠最多框架，而来自三个稳定边界：

1. **语义理解与事实验证分离**：Agent 解释用户，Provider 提供事实，Engine 判断可行性；
2. **计划作为版本化领域对象**：支持验证、Diff、回滚和最小扰动评测；
3. **业务核心与基础设施隔离**：地图、LLM、队列和工作流框架都可替换。

因此建议最先做的代码切片不是“五个 Agent”，而是：

```text
Create Trip
  → Add Wishlist POI
  → Fetch Route Matrix
  → Generate Deterministic Candidate
  → Validate
  → Persist Plan v1
  → Display Map + Timeline
```

该切片跑通后，再加入 Content Agent、Research Agent 和动态重规划，能显著降低“Agent 看似完成任务、系统却没有可靠业务内核”的风险。

