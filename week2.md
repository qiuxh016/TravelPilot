# TravelPilot Week 2：地图、POI 与确定性规划

目标：在 Week 1 的 Trip + Wishlist 基础上，接入真实地图数据，生成第一版不依赖 LLM 的可执行行程。

## 本周交付

用户可以搜索真实地点、加入 Wishlist、查看地点坐标和路线，并点击“生成计划”得到按天分组的基础行程和风险提示。

本周暂不做截图 OCR、社交平台自动解析、多 Agent、动态重规划和长期记忆。

## Day 1：Provider 接口和配置

创建：

    packages/core/src/travelpilot_core/ports/maps.py
    packages/infrastructure/src/travelpilot_infra/providers/maps/
    apps/api/src/travelpilot_api/schemas/maps.py

定义 MapsPort：

    search_place()
    get_place_detail()
    get_route()
    get_distance_matrix()

增加环境变量：

    MAPS_PROVIDER=amap
    AMAP_API_KEY=

要求地图 Key 只存在 .env，Core 只依赖 MapsPort，Provider 错误不能泄漏给前端。

验收：fake Provider 可以被测试，缺少 Key 时返回清晰错误。

## Day 2：POI 搜索和地点详情

API：

    GET /api/v1/places/search?keyword=熊猫基地&city=成都
    GET /api/v1/places/{provider_id}

扩展 POI 字段：

    provider
    provider_id
    opening_hours
    suggested_duration_min
    raw_metadata

前端创建：

    apps/web/app/places/
    apps/web/features/places/

验收：输入“熊猫基地”能返回候选并加入 Wishlist。

## Day 3：路线和距离矩阵

API：

    POST /api/v1/routes/estimate
    POST /api/v1/routes/matrix

实现地点间路线、缓存、Provider 超时和错误响应。相同查询不重复请求 Provider。

## Day 4：Plan 和 PlanNode

创建：

    trip_plans
    trip_plan_nodes
    plan_changes

API：

    POST /api/v1/trips/{trip_id}/plan-runs
    GET /api/v1/trips/{trip_id}/plans
    GET /api/v1/trips/{trip_id}/plans/{plan_id}

计划必须版本化，不覆盖旧计划。PlanNode 至少包含日期、开始时间、结束时间、POI、优先级和状态。

## Day 5：确定性规划器

创建：

    packages/core/src/travelpilot_core/engines/geo/clustering.py
    packages/core/src/travelpilot_core/engines/optimizer/itinerary.py
    packages/core/src/travelpilot_core/engines/constraints/validator.py

第一版流程：

    Wishlist POI
      → 按坐标粗分组
      → 按日期分配
      → 最近邻排序
      → 加入游玩时长和交通时间
      → 保留 Buffer
      → 生成 Plan

约束包括每日可用时间、游玩时长、通勤时间、每日 POI 数和营业时间。

## Day 6：前端 Plan Workspace

创建：

    apps/web/app/trips/[tripId]/plan/
    apps/web/features/itinerary/
    apps/web/features/map/

展示 Timeline、路线摘要、风险提示、计划版本和“重新生成”按钮。

## Day 7：回归和验收

执行：

    docker compose up -d postgres redis
    .\.venv\Scripts\alembic.exe upgrade head
    .\.venv\Scripts\python.exe -m pytest -q
    pnpm --dir apps/web build

Week 2 必须满足：

- 真实 POI 可以搜索；
- POI 可以加入 Wishlist；
- 可以查询路线和距离；
- 可以生成 Plan v1；
- Plan 有 Timeline；
- 基本时间和营业约束可验证；
- Provider 失败有可见错误。

