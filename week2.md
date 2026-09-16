# TravelPilot Week 2：地图、POI 与确定性规划（可执行实现）

本文是 Week 2 的完整操作手册，目标是跑通：`搜索 POI → 加入 Wishlist → 查询路线 → 生成版本化 Plan → Timeline 展示风险`。

## 0. 开始前检查

```powershell
Set-Location D:\codex\TravelPilot
docker compose up -d postgres redis
if (!(Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

## 1. 配置与地图 Port（Day 1）

`.env.example` 增加：

```dotenv
MAPS_PROVIDER=fake
AMAP_API_KEY=
MAPS_TIMEOUT_SECONDS=8
```

接高德时只在未提交的 `.env` 写 `MAPS_PROVIDER=amap` 和 `AMAP_API_KEY=...`。`pyproject.toml` 增加 `"httpx>=0.28,<1.0"`。

创建 `packages/core/src/travelpilot_core/ports/maps.py`：

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Place:
    provider: str
    provider_id: str
    name: str
    address: str | None
    category: str
    latitude: float
    longitude: float
    opening_hours: dict | None = None
    suggested_duration_min: int = 60
    raw_metadata: dict | None = None

@dataclass(frozen=True)
class Route:
    origin: tuple[float, float]
    destination: tuple[float, float]
    distance_m: int
    duration_min: int
    provider: str

class MapsPort(Protocol):
    async def search_place(self, keyword: str, city: str | None = None) -> list[Place]: ...
    async def get_place_detail(self, provider_id: str) -> Place: ...
    async def get_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> Route: ...
    async def get_distance_matrix(self, points: list[tuple[float, float]]) -> list[list[int]]: ...
```

创建 `packages/infrastructure/src/travelpilot_infra/providers/maps/errors.py`：

```python
class MapsProviderError(Exception): pass
class MapsConfigurationError(MapsProviderError): pass
class MapsUpstreamError(MapsProviderError): pass
```

创建 `packages/infrastructure/src/travelpilot_infra/providers/maps/fake.py`：

```python
import math
from travelpilot_core.ports.maps import Place, Route

PLACES = [
    Place("fake", "fake-panda", "成都大熊猫繁育研究基地", "成都市成华区熊猫大道1375号", "ATTRACTION", 30.7337, 104.1477, {"open":"07:30","close":"18:00"}, 180),
    Place("fake", "fake-kuanzhai", "宽窄巷子", "成都市青羊区长顺上街", "ATTRACTION", 30.6633, 104.0647, {"open":"00:00","close":"23:59"}, 120),
    Place("fake", "fake-wuhou", "武侯祠", "成都市武侯区武侯祠大街231号", "ATTRACTION", 30.6495, 104.0431, {"open":"09:00","close":"18:00"}, 120),
]
def distance(a,b): return int(math.hypot((a[0]-b[0])*111000,(a[1]-b[1])*96000))
class FakeMapsProvider:
    async def search_place(self, keyword, city=None): return [p for p in PLACES if keyword in p.name]
    async def get_place_detail(self, provider_id):
        for p in PLACES:
            if p.provider_id == provider_id: return p
        raise LookupError("place not found")
    async def get_route(self, origin, destination):
        d=distance(origin,destination); return Route(origin,destination,d,max(1,round(d/350)),"fake")
    async def get_distance_matrix(self, points): return [[distance(a,b)//350 for b in points] for a in points]
```

创建 `apps/api/src/travelpilot_api/settings.py`：

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    maps_provider: str = "fake"
    amap_api_key: str = ""
    maps_timeout_seconds: float = 8
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()
```

创建 `apps/api/src/travelpilot_api/dependencies.py`：

```python
from travelpilot_api.settings import settings
from travelpilot_infra.providers.maps.fake import FakeMapsProvider
from travelpilot_infra.providers.maps.amap import AmapProvider
def get_maps_provider():
    return AmapProvider(settings.amap_api_key, settings.maps_timeout_seconds) if settings.maps_provider == "amap" else FakeMapsProvider()
```

高德 Provider 使用 `httpx.AsyncClient(timeout=8)` 请求 place text/detail、driving、distance，并把 HTTP、JSON、上游业务错误统一转为 `MapsUpstreamError("地图服务暂时不可用")`；缺 Key 抛 `MapsConfigurationError("AMAP_API_KEY 未配置")`。不要把 URL、Key 或堆栈放进响应。

## 2. POI、路线 API（Day 2–3）

创建 `apps/api/src/travelpilot_api/schemas/maps.py`：

```python
from pydantic import BaseModel, Field
class PlaceRead(BaseModel):
    provider:str; provider_id:str; name:str; address:str|None; category:str
    latitude:float; longitude:float; opening_hours:dict|None=None
    suggested_duration_min:int=60; raw_metadata:dict|None=None
class RouteRequest(BaseModel):
    origin:tuple[float,float]; destination:tuple[float,float]
class RouteRead(BaseModel):
    distance_m:int; duration_min:int; provider:str
class MatrixRequest(BaseModel):
    points:list[tuple[float,float]]=Field(min_length=2,max_length=30)
class MatrixRead(BaseModel):
    durations_min:list[list[int]]; provider:str
```

创建 `apps/api/src/travelpilot_api/api/v1/maps.py`：

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.schemas.maps import *
from travelpilot_infra.providers.maps.errors import MapsProviderError
router=APIRouter(tags=["maps"])
def unavailable(exc): raise HTTPException(503, detail="地图服务暂时不可用") from exc
@router.get("/places/search",response_model=list[PlaceRead])
async def search(keyword:str=Query(min_length=1),city:str|None=None,maps=Depends(get_maps_provider)):
    try:return await maps.search_place(keyword,city)
    except MapsProviderError as e: unavailable(e)
@router.get("/places/{provider_id}",response_model=PlaceRead)
async def detail(provider_id:str,maps=Depends(get_maps_provider)):
    try:return await maps.get_place_detail(provider_id)
    except LookupError as e: raise HTTPException(404,"地点不存在") from e
    except MapsProviderError as e: unavailable(e)
@router.post("/routes/estimate",response_model=RouteRead)
async def estimate(p:RouteRequest,maps=Depends(get_maps_provider)):
    try:return await maps.get_route(p.origin,p.destination)
    except MapsProviderError as e: unavailable(e)
@router.post("/routes/matrix",response_model=MatrixRead)
async def matrix(p:MatrixRequest,maps=Depends(get_maps_provider)):
    try:return MatrixRead(durations_min=await maps.get_distance_matrix(p.points),provider=maps.__class__.__name__)
    except MapsProviderError as e: unavailable(e)
```

在 `main.py` 导入 router 并执行 `app.include_router(maps_router, prefix="/api/v1")`。缓存 key 使用 provider、方法和规范化参数，TTL 10 分钟；路线和矩阵相同查询不得重复请求 Provider。

## 3. Plan 数据库和版本 API（Day 4）

新建 `packages/infrastructure/src/travelpilot_infra/db/models/plan.py`，定义 `TripPlanModel(id, trip_id, version, status, warnings JSONB, created_at)` 与 `TripPlanNodeModel(id, plan_id, poi_id, day, start_time, end_time, priority, status, note)`；外键指向 trip/plan/poi，`(trip_id, version)` 唯一。同步在 `models/__init__.py` 导入两个模型。POI 增加：

```python
raw_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

生成迁移：

```powershell
.\.venv\Scripts\alembic.exe revision --autogenerate -m "add plans and poi metadata"
.\.venv\Scripts\alembic.exe upgrade head
```

实现 `POST /api/v1/trips/{trip_id}/plan-runs`、`GET /api/v1/trips/{trip_id}/plans`、`GET /api/v1/trips/{trip_id}/plans/{plan_id}`。事务内取 Wishlist，版本为 `max(version)+1`，只 INSERT 新 Plan/Nodes，不覆盖旧版本；空 Wishlist 返回 400 `WISHLIST_EMPTY`，Provider 错误返回 503 `MAPS_PROVIDER_UNAVAILABLE`。

## 4. 确定性规划器（Day 5）

创建 `packages/core/src/travelpilot_core/engines/optimizer/itinerary.py`：

```python
from datetime import datetime,time,timedelta
def build_itinerary(trip,items,travel_min=20,buffer_min=60):
    result=[]; warnings=[]; days=(trip.end_date-trip.start_date).days+1
    for i,item in enumerate(sorted(items,key=lambda x:(-x.priority,-float(x.interest_score or 0)))):
        day=trip.start_date+timedelta(days=i%days)
        start=datetime.combine(day,time(9))+timedelta(minutes=(i//days)*travel_min)
        end=start+timedelta(minutes=item.poi.suggested_duration_min)
        if end.time()>time(21)-timedelta(minutes=buffer_min):
            warnings.append(f"{day} 无法容纳：{item.poi.name}"); continue
        opening=item.poi.opening_hours or {}
        if opening.get("open") and start.time()<time.fromisoformat(opening["open"]):
            start=datetime.combine(day,time.fromisoformat(opening["open"]))
            end=start+timedelta(minutes=item.poi.suggested_duration_min)
        if opening.get("close") and end.time()>time.fromisoformat(opening["close"]):
            warnings.append(f"{item.poi.name} 超出营业时间"); continue
        result.append({"day":day,"start_time":start.time(),"end_time":end.time(),"item":item})
    return result,warnings
```

另建 `engines/geo/clustering.py`，按 `(latitude // 0.05, longitude // 0.05)` 粗分组；建 `constraints/validator.py` 校验每日 09:00–21:00、60 分钟 Buffer、每日最多 6 个 POI、游玩/交通/营业时间。规划顺序固定为：分组 → 日期分配 → 最近邻/稳定排序 → 加时长 → Buffer → warnings。

## 5. 前端闭环（Day 6）

在 `apps/web/lib/api.ts` 追加：

```typescript
export type Place={provider:string;provider_id:string;name:string;address:string|null;category:string;latitude:number;longitude:number;suggested_duration_min:number};
export type PlanNode={day:string;start_time:string;end_time:string;name:string;priority:number;status:string};
export type Plan={id:string;version:number;status:string;warnings:string[];nodes:PlanNode[]};
export const searchPlaces=(q:string,c:string)=>request<Place[]>(`/api/v1/places/search?keyword=${encodeURIComponent(q)}&city=${encodeURIComponent(c)}`);
export const createPlanRun=(id:string)=>request<Plan>(`/api/v1/trips/${id}/plan-runs`,{method:"POST"});
export const listPlans=(id:string)=>request<Plan[]>(`/api/v1/trips/${id}/plans`);
```

创建 `apps/web/app/places/page.tsx`：keyword/city 搜索，展示名称、地址、坐标和“加入 Wishlist”，加入时调用已有 `addWishlistItem`。创建 `apps/web/app/trips/[tripId]/plan/page.tsx`：按 day 分组展示时间、名称、优先级、状态，显示 warnings、版本和重新生成按钮。地图瓦片留到 Week 3。

## 6. 测试与验收（Day 7）

创建 `apps/api/tests/test_maps.py`：

```python
import pytest
from travelpilot_infra.providers.maps.fake import FakeMapsProvider
@pytest.mark.asyncio
async def test_fake_search_and_route():
    p=FakeMapsProvider(); places=await p.search_place("熊猫")
    assert places[0].provider_id=="fake-panda"
    route=await p.get_route((30.73,104.14),(30.66,104.06))
    assert route.distance_m>0 and route.duration_min>0
```

执行：

```powershell
docker compose up -d postgres redis
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m compileall -q apps packages
.\.venv\Scripts\python.exe -m pytest -q
pnpm --dir apps/web build
Invoke-RestMethod 'http://localhost:8000/api/v1/places/search?keyword=熊猫基地&city=成都'
```

必须确认：POI 可搜索并加入 Wishlist；路线/矩阵可查且缓存命中；Plan v1、v2 均可查且 v1 不变；Timeline 展示节点；时间、Buffer、每日数量、营业冲突产生 warnings；Provider 失败为可见 503 且不泄漏 Key/堆栈。OCR、社交解析、多 Agent、LLM、动态重规划、长期记忆和地图瓦片不在本周范围。

---

## 13. 执行位置、文件职责与设计原因

所有 PowerShell 命令都在 `D:\codex\TravelPilot` 根目录执行。VS Code 也应直接打开这个根目录，不要分别打开 `apps/api` 或 `packages/core`。Week 1 已经存在 POI、Wishlist 和 Alembic 基础，因此先执行下面的检查，避免重复创建同名表：

```powershell
Set-Location D:\codex\TravelPilot
rg --files apps packages
Get-Content -Raw packages/infrastructure/src/travelpilot_infra/db/models/__init__.py
Get-Content -Raw packages/infrastructure/src/travelpilot_infra/db/migrations/env.py
git status --short
```

本周文件职责是：`packages/core` 只放 MapsPort、聚类、排程和约束；`packages/infrastructure` 放 Fake/高德 Provider、数据库模型和缓存；`apps/api` 放 Pydantic Schema、依赖注入和 HTTP 路由；`apps/web` 只负责调用 API 和展示。这样做是为了让 Core 不依赖 FastAPI、SQLAlchemy、Redis 或高德 SDK，将来替换地图供应商时不需要重写规划器。

## 14. Day 1：逐文件创建和即时验证

```powershell
New-Item -ItemType Directory -Force packages/core/src/travelpilot_core/ports
New-Item -ItemType Directory -Force packages/core/src/travelpilot_core/engines/geo
New-Item -ItemType Directory -Force packages/core/src/travelpilot_core/engines/optimizer
New-Item -ItemType Directory -Force packages/core/src/travelpilot_core/engines/constraints
New-Item -ItemType Directory -Force packages/infrastructure/src/travelpilot_infra/providers/maps
New-Item -ItemType File -Force packages/core/src/travelpilot_core/ports/__init__.py
New-Item -ItemType File -Force packages/infrastructure/src/travelpilot_infra/providers/maps/__init__.py
```

然后严格按照第 1 节的路径创建每个文件，并在每创建一个 Python 文件后执行：

```powershell
.\.venv\Scripts\python.exe -m compileall -q apps packages
```

先做 Fake Provider 是因为真实 API 依赖 Key、网络和额度，不能成为单元测试前置条件。Fake Provider 使用固定数据实现同一个 MapsPort，所以既可以测试 API，也可以测试规划器。

## 15. Day 2：API 的逐步验证

启动 API 的终端保持打开：

```powershell
uvicorn apps.api.src.travelpilot_api.main:app --reload
```

另开 PowerShell 搜索 POI：

```powershell
$places = Invoke-RestMethod "http://localhost:8000/api/v1/places/search?keyword=熊猫基地&city=成都"
$places | ConvertTo-Json -Depth 5
```

预期返回 `fake-panda`。路线接口：

```powershell
$body = @{ origin = @(30.7337,104.1477); destination = @(30.6633,104.0647) } | ConvertTo-Json
Invoke-RestMethod "http://localhost:8000/api/v1/routes/estimate" -Method Post -ContentType "application/json" -Body $body
```

如果 404，检查 `main.py` 是否同时完成 router import 和 `include_router`；如果 500，先查看 Uvicorn 终端的第一条 traceback，不要先修改前端。

## 16. Plan 版本化和确定性规划的原因

一次生成结果是可审计快照，不是 Trip 的一个 JSON 字段：

```text
Trip 成都 4 日游
├── Plan v1：第一次生成
├── Plan v2：修改 Wishlist 后重新生成
└── Plan v3：未来动态重规划
```

`POST /plan-runs` 必须 INSERT 新 Plan 和新 Nodes，不能 UPDATE 旧 Plan；`(trip_id, version)` 必须有唯一约束。这样用户才能比较、回滚并解释行程变化。

营业时间、游玩时长、交通时间、每日 POI 数和 Buffer 都是可验证规则，不应交给 LLM 猜测。固定流程是：

```text
坐标粗分组 → 按天分配 → 最近邻/稳定排序 → 加游玩和交通时间
→ 保留 Buffer → validator 生成 warnings → 持久化 Plan
```

## 17. 完整实现的边界测试

除了 Fake Provider 测试，还要覆盖：空 keyword 返回 422、未知地点返回 404、Provider 异常返回 503、空 Wishlist 返回 400、第一次生成 v1、第二次生成 v2 且 v1 不变、超过每日时间或营业时间时产生 warnings。文档写入代码不等于仓库已经实现，最终必须以文件真实存在、Alembic 成功、pytest 通过、Web build 通过和 Swagger 手工调用成功为准。
