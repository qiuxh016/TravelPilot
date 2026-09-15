# TravelPilot 项目初步规划

## 1. 项目概述

### 1.1 项目名称
**TravelPilot**

### 1.2 项目定位
TravelPilot 是一个面向国内自由行场景的 **Context-Aware Multi-Agent Travel Copilot**。

它的核心目标不是简单“生成一份旅游攻略”，而是：

> 将用户散落在小红书、B站、地图、截图、聊天记录、订单等不同来源中的旅行信息，整理成可执行的旅行计划，并在天气、时间、体力、营业状态、临时变更等现实条件发生变化时，持续进行局部重规划。

### 1.3 核心价值
当前国内自由行通常存在以下问题：

- 攻略、收藏、餐厅、景点、酒店、交通等信息分散在多个平台；
- 用户收藏很多内容，但很难将“收藏”真正转化为“可执行行程”；
- 普通 LLM 生成的旅游计划通常缺乏真实地图距离、营业时间、预约、天气等约束；
- 行程一旦发生下雨、起晚、排队、景点关闭、临时疲劳等情况，用户需要手动重新调整；
- 多次旅行之间缺乏长期偏好学习和个性化记忆。

TravelPilot 希望解决：

**信息碎片化 + 多约束规划 + 动态变化 + 长期个性化**

---

## 2. 产品目标

### 2.1 第一阶段目标
第一阶段聚焦 **国内自由行**，不追求覆盖所有旅游服务。

核心闭环：

```text
收藏 / 链接 / 截图 / 文本
        ↓
     Wishlist
        ↓
  Constraint-aware Planning
        ↓
      Plan Check
        ↓
     Trip Execution
        ↓
     Real-world Event
        ↓
   Local Replanning
        ↓
      Feedback
        ↓
      Memory
```

### 2.2 暂不做
V1 阶段不重点实现：

- 自动购买机票
- 自动购买高铁票
- 自动完成酒店支付
- 自动餐厅预订
- 全网最低价比价
- 大规模爬取小红书 / 大众点评 / 美团 / 携程
- 全球旅行数据适配
- 复杂多人财务结算

这些能力可以在后续版本中逐步扩展。

---

# 3. 目标用户

## 3.1 核心用户

主要面向：

- 国内自由行用户
- 20–35 岁年轻旅行者
- 经常通过小红书 / B站 / 抖音 / 地图搜攻略的人
- 喜欢收藏大量 POI，但缺乏系统整理的人
- 自己做行程但经常需要反复修改的人

## 3.2 典型用户场景

例如：

> 用户计划国庆从广州去成都旅行 4 天，两个人预算 6000 元，希望吃美食、拍照、不想特种兵，已经在小红书收藏了十几个地方。

用户可以向 TravelPilot 提供：

- 小红书分享链接
- B站链接
- 攻略截图
- 微信文字
- 酒店订单截图
- 高铁 / 航班订单截图
- 自己输入的 Wishlist

系统将这些内容统一转换成结构化 Trip Context。

---

# 4. 核心产品功能

## 4.1 收藏聚合与旅行愿望池

### 输入
支持：

- URL
- 截图
- 文本
- 手动输入 POI
- 订单截图
- 攻略笔记

### 处理流程

```text
Raw Content
    ↓
Content Parser
    ↓
Entity Extraction
    ↓
POI / Restaurant / Hotel / Advice / Reservation
    ↓
Entity Linking
    ↓
Map POI
    ↓
Travel Wishlist
```

### 结构化结果

例如：

```text
成都旅行 Wishlist

景点：8
餐厅：9
咖啡：3
购物：3
```

每个地点可包含：

```text
name
category
latitude
longitude
address
opening_hours
suggested_duration
reservation_required
ticket_required
indoor
weather_sensitive
price
crowd_level
tags
source
user_interest_score
```

---

# 5. 智能旅行规划

## 5.1 Hybrid Planning

TravelPilot 不让 LLM 独立决定完整行程，而采用：

**LLM + Constraint Engine + Optimization**

### LLM 负责

- 理解自然语言需求
- 提取用户偏好
- 将模糊表达转换成结构化参数

例如：

```text
“不想太累”
→ pace = relaxed

“喜欢吃东西”
→ food_priority = high

“晚上想逛街”
→ night_activity = high
```

### Constraint Engine 负责

Hard Constraints：

- 酒店入住时间
- 航班 / 高铁时间
- 景点营业时间
- 预约时间
- 地图交通时间
- 预算
- 城市 / 区域限制

Soft Constraints：

- 用户兴趣
- 步行容忍度
- 特种兵程度
- 饮食偏好
- 拍照需求
- 夜生活偏好
- 室内 / 室外偏好

### Optimizer 负责

- POI 分天
- 地理聚类
- 路线排序
- 时间分配
- Buffer 安排
- Utility 优化

---

# 6. 特种兵程度

设计一个旅行节奏参数：

```text
躺平  1 ─ 2 ─ 3 ─ 4 ─ 5  特种兵
```

不同等级对应：

| 等级 | POI / 天 | 步行 | Buffer | 行程密度 |
|---|---:|---:|---:|---|
| 1 | 2–3 | < 8 km | 大 | 低 |
| 2 | 3–4 | < 10 km | 较大 | 较低 |
| 3 | 4–5 | < 15 km | 中 | 中 |
| 4 | 5–7 | < 20 km | 小 | 高 |
| 5 | 7+ | 20 km+ | 很小 | 很高 |

用户可以在旅行中动态调整：

> “今天太累了。”

系统自动：

```text
energy = low
pace = 3 → 1
walking_penalty ↑
POI_count ↓
rest_time ↑
```

---

# 7. Plan Check

用户可以不让 AI 生成行程，而直接上传已有行程。

TravelPilot 对现有计划进行检查：

```text
Existing Plan
      ↓
Trip Parser
      ↓
Constraint Engine
      ↓
Maps / Weather / POI
      ↓
Conflict Detection
```

可检测：

- 路线不合理
- 时间冲突
- 景点已关闭
- 营业时间不匹配
- 预约缺失
- 通勤时间不足
- 行程密度过高
- 天气不适合
- 连续高强度行程

输出：

```text
✓ Feasible
⚠ Risk
✗ Conflict
```

---

# 8. Dynamic Local Replanning

这是 TravelPilot 最核心的技术点之一。

现实事件：

- 下雨
- 起晚
- 用户疲劳
- 景点关闭
- 餐厅排队
- 交通延迟
- 临时新增地点
- 临时取消地点

传统方式：

```text
整个行程重新生成
```

TravelPilot：

```text
Event
  ↓
Impact Analysis
  ↓
Affected DAG Nodes
  ↓
Constraint Propagation
  ↓
Local Replanning
  ↓
Validation
  ↓
Plan Patch
```

目标：

**Minimal-Disruption Replanning**

即尽量只修改受影响的部分。

可构造优化目标：

```text
minimize

α × ChangeCost
+ β × TravelTime
+ γ × BudgetCost
- δ × UserUtility
```

---

# 9. Traveler Memory

Memory 不仅保存聊天记录，而是建立长期 Traveler Profile。

## 9.1 Explicit Preference

用户主动表达：

```text
喜欢美食
不喜欢早起
不喜欢走太多
喜欢拍照
```

## 9.2 Behavioral Preference

通过实际旅行行为推断：

```text
计划博物馆 4 个
实际完成 2 个

计划夜市 1 小时
实际停留 3 小时
```

系统更新：

```text
Museum Preference ↓
Night Market Preference ↑
```

## 9.3 Constraint Memory

记录：

- 步行容忍度
- 起床时间
- 每天可接受 POI 数
- 饮食时间
- 午休习惯
- 预算偏好

## 9.4 Episodic Memory

记录历史旅行：

```text
Trip
Context
Plan
Events
Changes
Actual Behavior
Feedback
```

长期形成：

**Traveler Model**

---

# 10. Multi-Agent 设计

V1 不建议设计过多 Agent。

初步采用 5 个核心 Agent。

## 10.1 Content Agent

负责：

- URL / Screenshot / Text 解析
- POI 信息抽取
- 攻略建议抽取
- Reservation 信息识别
- Entity Linking

---

## 10.2 Research Agent

负责：

- POI 补充信息
- 营业时间
- 是否预约
- 推荐游玩时间
- 室内 / 室外判断
- Web 信息检索

---

## 10.3 Planner Agent

负责：

- 用户需求理解
- Trip DAG 生成
- 调用 Constraint Engine
- 调用 Route / Geo Tool
- 生成 Candidate Plan

---

## 10.4 Validator Agent

负责：

- Constraint Validation
- 路线验证
- 时间验证
- 营业验证
- 天气验证
- 预约验证
- 风险识别

---

## 10.5 Replanner Agent

负责：

- Event Impact Analysis
- 找出受影响节点
- 局部重新规划
- 尽量减少原计划修改

---

# 11. MCP Tool Layer

所有外部数据源通过 MCP / Tool Gateway 统一接入。

```text
Agent Runtime
      ↓
MCP Gateway
      ↓
├── Maps MCP
├── Weather MCP
├── Search MCP
├── User Context MCP
├── Trip Data MCP
└── Future Provider MCP
```

## 11.1 Maps MCP

计划优先使用高德开放平台。

Tools：

```text
search_place()
get_place_detail()
geocode()
get_route()
get_distance_matrix()
search_nearby()
```

---

## 11.2 Weather MCP

可使用：

- 高德天气
- 和风天气

Tools：

```text
get_current_weather()
get_daily_forecast()
get_hourly_forecast()
get_weather_alert()
```

---

## 11.3 Search MCP

负责：

- 景点信息
- 预约规则
- 节假日开放情况
- 攻略补充
- 临时事件

---

## 11.4 User Context MCP

负责：

```text
get_user_preferences()
get_trip_context()
get_reservations()
get_travel_history()
get_behavior_memory()
```

---

# 12. 国内数据源规划

| 数据 | V1 来源 | 状态 |
|---|---|---|
| 地图 | 高德开放平台 | 核心 |
| POI | 高德 | 核心 |
| 路线 | 高德 | 核心 |
| 距离矩阵 | 高德 | 核心 |
| 天气 | 高德 / 和风天气 | 核心 |
| 攻略 | Web Search + User Content | 核心 |
| 用户收藏 | URL / Screenshot / Text | 核心 |
| 酒店 | 用户订单 / Search | V1 简化 |
| 航班 | 用户输入 / 订单解析 | V1 简化 |
| 高铁 | 用户输入 / 订单解析 | V1 简化 |
| 餐厅 | POI + Search | 支持 |
| 小红书 | 用户主动分享内容 | 支持 |
| B站 | 用户链接 | 支持 |
| 大众点评 | 不依赖直接爬取 | 暂缓 |
| 美团 | 不依赖直接爬取 | 暂缓 |
| 携程 | 不依赖直接爬取 | 暂缓 |
| 12306 | 不做自动购票 | 暂缓 |

原则：

> Official API + User-provided Content + Search + Adapter Interface

避免把项目建立在不稳定爬虫之上。

---

# 13. Trip DAG

旅行计划内部建议使用 DAG / Structured Plan，而不是纯文本。

例如：

```text
Trip
├── Day 1
│   ├── Hotel Check-in
│   ├── POI A
│   ├── Lunch
│   ├── POI B
│   └── Dinner
│
├── Day 2
│   ├── POI C
│   ├── POI D
│   └── ...
```

Node：

```text
TripNode {
    id
    type
    poi_id
    start_time
    end_time
    duration
    location
    dependencies
    constraints
    status
    flexibility
    priority
}
```

这为后续：

- Conflict Detection
- Impact Analysis
- Local Replanning
- Workflow Resume

提供基础。

---

# 14. 数据库初步设计

## PostgreSQL

核心表：

```text
users
trips
trip_members
trip_preferences
pois
trip_wishlist
trip_plans
trip_plan_nodes
trip_events
reservations
user_feedback
travel_memories
agent_runs
tool_calls
workflow_runs
```

---

## Redis

用于：

- Agent Working Memory
- Session State
- Tool Cache
- Workflow Intermediate State
- Temporary Lock

---

## Vector DB

可选：

- Qdrant
- pgvector

存储：

- 攻略 Embedding
- 用户收藏内容
- Episodic Memory
- 用户历史反馈
- POI 语义信息

V1 可优先使用：

**PostgreSQL + pgvector**

降低系统复杂度。

---

# 15. 技术架构

```text
                Frontend
                   │
                   ▼
              API Gateway
                   │
                   ▼
              Agent Runtime
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
 Workflow       Memory        Policy
 Engine         Manager       Engine
     │             │             │
     └─────────────┼─────────────┘
                   ▼
                Planner
                   │
                   ▼
             MCP Tool Layer
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
     Map        Weather       Search
      │            │            │
      └────────────┼────────────┘
                   ▼
              External Data
```

---

# 16. 推荐技术栈

## Backend

```text
Python
FastAPI
Pydantic
AsyncIO
```

## Agent Runtime

建议：

- Agent Runtime 自己实现核心逻辑
- 使用 LLM SDK
- MCP SDK
- Structured Output
- Tool Calling

不建议直接让 LangGraph / CrewAI / AutoGen 成为整个系统的核心。

可以参考成熟框架，但自己实现：

- Agent Loop
- Planner
- Workflow
- State
- Retry
- Replan
- Tool Router

---

## Database

```text
PostgreSQL
pgvector
Redis
```

---

## Frontend

可以考虑：

```text
Next.js
React
TypeScript
Tailwind CSS
Map SDK
```

核心 UI：

- Trip Workspace
- Wishlist Map
- Itinerary Timeline
- Plan Check
- Replanning Diff
- Traveler Profile

---

# 17. 前端页面规划

## 17.1 Home

输入：

- 目的地
- 日期
- 同行人数
- 预算
- 节奏
- 兴趣

---

## 17.2 Trip Workspace

包含：

```text
左侧：
Wishlist / 收藏

中间：
Map

右侧：
Day Timeline
```

---

## 17.3 Wishlist Map

展示：

- POI
- 分类
- Geo Cluster
- 收藏来源
- 用户兴趣度

---

## 17.4 Itinerary

展示：

```text
Day 1

09:00 成都熊猫基地
11:30 Lunch
14:00 春熙路
16:00 太古里
...
```

同时展示：

- 路线
- 时间
- 风险
- 天气
- 预约

---

## 17.5 Plan Check

显示：

```text
✓ Valid
⚠ Risk
✗ Conflict
```

---

## 17.6 Replanning View

展示：

```text
Original Plan
      vs
Updated Plan
```

高亮：

- 保留
- 删除
- 新增
- 时间变化

---

# 18. Evaluation

这是项目区别于普通 Demo 的关键。

## 18.1 Planning Metrics

- Constraint Violation Rate
- Route Efficiency
- Total Travel Time
- Budget Violation
- Opening-hour Conflict
- POI Coverage
- User Preference Satisfaction

---

## 18.2 Replanning Metrics

- Replanning Success Rate
- Changed Node Count
- Change Cost
- Constraint Recovery Rate
- Replanning Latency

核心指标：

**Minimal Disruption**

---

## 18.3 Agent Metrics

- Task Success Rate
- Tool Call Success Rate
- Tool Error Recovery
- Average Tool Calls
- Token Cost
- Latency
- Retry Rate

---

## 18.4 Ablation

可比较：

```text
LLM-only

vs

LLM + Tools

vs

LLM + Constraint Engine

vs

LLM + Constraint Engine + Validator

vs

Full TravelPilot + Replanning
```

---

# 19. MVP 范围

第一阶段只确保以下流程完整：

## Scenario

用户计划：

> 成都 4 日自由行。

提供：

- 若干小红书 / B站链接
- 截图
- Wishlist
- 酒店信息
- 高铁 / 航班时间
- 用户偏好

TravelPilot 完成：

```text
1. Content Parsing
2. POI Extraction
3. Map Entity Linking
4. Wishlist
5. Geo Clustering
6. Constraint-aware Planning
7. Route Validation
8. Weather-aware Validation
9. Plan Check
10. Dynamic Replanning
11. Feedback
12. Memory Update
```

如果这个闭环做完整，已经足够作为第一版。

---

# 20. 开发阶段规划

## Phase 0：需求与架构

目标：

- PRD
- User Flow
- Architecture
- DB Schema
- Trip DAG Schema
- Tool Schema

---

## Phase 1：基础地图旅行系统

实现：

- Trip CRUD
- POI Search
- Map
- Route
- Weather
- Wishlist

目标：

形成无 Agent 的基础旅行系统。

---

## Phase 2：Content Agent

实现：

- Text Parsing
- Screenshot Parsing
- Link Parsing
- POI Extraction
- Entity Linking

---

## Phase 3：Planning Engine

实现：

- User Preference Parser
- Geo Clustering
- Constraint Engine
- Route Matrix
- Candidate Plan
- Validator

---

## Phase 4：Agent Runtime

实现：

- Agent Loop
- Tool Router
- MCP
- Workflow
- Retry
- Structured Output
- State Management

---

## Phase 5：Dynamic Replanning

实现：

- Event Schema
- Impact Analysis
- DAG Dependency
- Local Replanning
- Plan Diff

---

## Phase 6：Memory

实现：

- Preference Memory
- Behavior Memory
- Episodic Memory
- Memory Retrieval
- Memory Update

---

## Phase 7：Evaluation + Demo

实现：

- Test Dataset
- Planning Metrics
- Ablation
- Failure Cases
- Demo Video
- README
- Architecture Diagram

---

# 21. 项目技术亮点

最终希望项目能突出以下 3–4 个真正有深度的技术点。

## Highlight 1

**Constraint-aware Multi-Agent Planning**

自然语言需求：

```text
→ Structured Preference
→ Constraint Solver
→ Route Optimization
→ Feasible Plan
```

---

## Highlight 2

**Event-driven Local Replanning**

现实世界发生变化后：

```text
Event
→ Impact Analysis
→ Affected DAG
→ Local Replanning
→ Constraint Validation
```

避免整个计划重新生成。

---

## Highlight 3

**MCP-based Multi-source Tool Layer**

通过 MCP 解耦：

```text
Agent
→ MCP
→ Maps / Weather / Search / User Context
```

支持未来快速替换 Provider。

---

## Highlight 4

**Adaptive Traveler Memory**

结合：

```text
Explicit Preference
+
Observed Behavior
+
Trip History
+
Feedback
```

不断更新 Traveler Profile。

---

# 22. 项目简历描述方向

后续可考虑将项目描述浓缩为：

> Designed and implemented a context-aware multi-agent travel copilot for domestic free travel, integrating map, weather, POI and user-generated travel content through an MCP-based tool layer.

> Built a hybrid LLM + constraint optimization planner that converts natural-language preferences into executable itinerary DAGs while validating route, time, opening-hour and weather constraints.

> Developed an event-driven local replanning mechanism that identifies affected itinerary nodes and minimizes plan disruption under weather, delay and user-state changes.

> Implemented hierarchical traveler memory combining explicit preferences, behavioral signals and episodic trip history for long-term personalization.

---

# 23. 后续扩展

## V2

- 酒店实时搜索
- 航班搜索
- 高铁数据
- 门票
- 餐厅预订
- 多人旅行
- Preference Negotiation
- Crowd Prediction
- 实时交通
- Calendar
- Expense Tracking

## V3

扩展国际旅行：

```text
高德
→ Google Maps

国内天气
→ Global Weather Provider

国内 Provider
→ Booking / Amadeus / Skyscanner
```

由于 Tool Layer 使用 Adapter / MCP 抽象，Agent 层不需要大规模修改。

---

# 24. 当前项目核心定义

最终第一阶段建议把 TravelPilot 定义为：

> **一个面向国内自由行的 Multi-Agent Travel Copilot：将用户分散的旅行收藏和真实世界数据转化为可执行行程，并通过 Constraint-aware Planning、Event-driven Local Replanning 和 Adaptive Traveler Memory，让旅行计划随着现实变化持续保持可行。**

第一阶段最重要的不是覆盖最多平台，而是把以下闭环做深：

```text
Collect
   ↓
Understand
   ↓
Plan
   ↓
Validate
   ↓
Execute
   ↓
Observe
   ↓
Replan
   ↓
Remember
```

这将作为后续产品、工程和算法设计的主线。


---

# 25. PRD：核心用户流程

## 25.1 创建一次旅行

用户进入系统后创建 Trip：

```text
创建旅行
   ↓
输入目的地 / 日期 / 同行人数
   ↓
填写预算 / 旅行节奏 / 兴趣偏好
   ↓
导入已有交通与酒店信息
   ↓
进入 Trip Workspace
```

基础字段：

```text
destination
start_date
end_date
departure_city
members
budget
pace_level
interests[]
hotel
transportation
special_constraints[]
```

---

## 25.2 导入收藏

用户可以通过：

- 复制链接
- 上传截图
- 输入文字
- 手动添加地点

进行收藏导入。

系统流程：

```text
Input
 ↓
Parse
 ↓
Extract Entity
 ↓
Link to Map POI
 ↓
Verify
 ↓
Add to Wishlist
```

如果无法确定 POI，应返回候选供用户选择，而不是强行匹配。

---

## 25.3 生成初始计划

用户点击：

> Generate Plan

系统：

```text
Trip Context
+
Wishlist
+
Traveler Profile
+
Map / Weather / POI
        ↓
Preference Parser
        ↓
Geo Clustering
        ↓
Candidate Generation
        ↓
Constraint Solver
        ↓
Route Validation
        ↓
Plan Scoring
        ↓
Best Plan
```

输出：

- 每日 Timeline
- 地图路线
- 每个 POI 的时间段
- 餐饮安排
- Buffer
- 风险提示
- 预约提示

---

## 25.4 修改计划

用户可以通过自然语言：

> “第二天下午不要安排博物馆。”

> “每天最多安排四个地点。”

> “想把春熙路放到晚上。”

系统不重新生成整个 Trip，而是：

```text
User Modification
      ↓
Constraint Update
      ↓
Affected Nodes
      ↓
Partial Replan
      ↓
Validation
```

---

## 25.5 旅行中模式

Trip 开始后进入：

**Live Trip Mode**

系统显示：

- 当前时间
- 当前地点
- 下一站
- 路程
- 天气
- 风险
- 今日剩余安排

用户可以输入：

> “我累了。”

> “现在下雨。”

> “这个地方不去了。”

> “附近有没有吃的？”

系统触发 Context-Aware Replanning。

---

# 26. 核心场景定义

为了避免项目范围无限扩大，V1 明确支持以下五类核心场景。

## Scenario A：收藏变行程

用户：

> 我收藏了很多成都攻略，帮我整理成 4 天计划。

目标：

```text
Unstructured Content
→ POI Pool
→ Geo Cluster
→ Plan
```

---

## Scenario B：已有行程体检

用户上传已有计划。

系统识别：

- 路程过长
- 景点闭馆
- 时间不够
- 行程太赶
- 重复区域
- 预约风险

---

## Scenario C：天气变化

例如：

```text
15:00–18:00
Heavy Rain
```

系统检测受影响的 Outdoor POI，并进行局部替换。

---

## Scenario D：用户状态变化

用户：

> 今天很累，只想轻松一点。

系统动态修改：

```text
pace_level ↓
walking_limit ↓
poi_count ↓
indoor / cafe preference ↑
```

---

## Scenario E：临时插入地点

用户：

> 朋友推荐了一个火锅店，今晚想去。

系统：

```text
New Node
 ↓
Find Available Slot
 ↓
Route Impact
 ↓
Conflict Detection
 ↓
Patch Plan
```

---

# 27. 系统模块边界

整体建议拆成六层。

```text
┌─────────────────────────────────┐
│          Presentation Layer      │
│ Web / Mobile / Map / Timeline    │
└─────────────────┬───────────────┘
                  ↓
┌─────────────────────────────────┐
│        Application Layer         │
│ Trip / Wishlist / Plan / Event   │
└─────────────────┬───────────────┘
                  ↓
┌─────────────────────────────────┐
│          Agent Runtime           │
│ Planner / Research / Validator   │
│ Replanner / Content Agent        │
└─────────────────┬───────────────┘
                  ↓
┌─────────────────────────────────┐
│       Deterministic Engines      │
│ Constraint / Geo / Route / Score │
└─────────────────┬───────────────┘
                  ↓
┌─────────────────────────────────┐
│       Tool & MCP Gateway         │
│ Maps / Weather / Search / User   │
└─────────────────┬───────────────┘
                  ↓
┌─────────────────────────────────┐
│            Data Layer            │
│ PostgreSQL / pgvector / Redis    │
└─────────────────────────────────┘
```

核心原则：

> LLM 负责理解和决策建议，确定性模块负责验证真实世界约束。

---

# 28. Agent Runtime 设计

## 28.1 Agent 基础接口

所有 Agent 建议统一接口：

```python
class Agent:
    async def run(
        self,
        task,
        context,
        tools,
        memory
    ) -> AgentResult:
        ...
```

AgentResult：

```text
status
output
tool_calls
reasoning_summary
artifacts
next_action
errors
```

---

## 28.2 Agent Loop

```text
Task
 ↓
Build Context
 ↓
LLM Reasoning
 ↓
Tool Selection
 ↓
Tool Execution
 ↓
Observation
 ↓
Continue / Finish / Replan
```

需要支持：

- 最大循环次数
- Token Budget
- Tool Timeout
- Retry
- Structured Output
- Error Recovery

---

# 29. Workflow Engine

Workflow 不完全交给 Agent 自由控制。

采用：

**Deterministic Workflow + Agentic Decision**

示例：

```text
INGEST
  ↓
EXTRACT
  ↓
LINK_POI
  ↓
ENRICH
  ↓
PLAN
  ↓
VALIDATE
  ↓
REPAIR
  ↓
READY
```

旅行过程中：

```text
ACTIVE
 ↓
EVENT_RECEIVED
 ↓
IMPACT_ANALYSIS
 ↓
REPLAN
 ↓
VALIDATE
 ↓
PATCHED
```

Workflow Engine 后续支持：

- checkpoint
- retry
- timeout
- resume
- parallel execution
- dependency management

---

# 30. Trip 状态机

建议 Trip 使用显式状态。

```text
DRAFT
 ↓
COLLECTING
 ↓
PLANNING
 ↓
VALIDATING
 ↓
READY
 ↓
ACTIVE
 ↓
COMPLETED
```

异常状态：

```text
NEEDS_USER_INPUT
PLAN_CONFLICT
TOOL_ERROR
CANCELLED
```

这样前端和 Agent Runtime 都更容易管理。

---

# 31. Trip DAG 详细设计

## 31.1 Node Type

建议：

```text
TRANSPORT
HOTEL
ATTRACTION
RESTAURANT
CAFE
SHOPPING
REST
FREE_TIME
CUSTOM
```

---

## 31.2 Node 数据结构

```json
{
  "id": "node_xxx",
  "trip_id": "trip_xxx",
  "day": 2,
  "type": "ATTRACTION",
  "poi_id": "poi_xxx",
  "title": "成都大熊猫繁育研究基地",

  "start_time": "2026-10-02T08:00:00",
  "end_time": "2026-10-02T11:00:00",

  "duration_min": 180,

  "priority": 0.92,
  "flexibility": 0.30,

  "status": "PLANNED",

  "constraints": {
    "opening_hours": true,
    "reservation_required": true,
    "weather_sensitive": false
  },

  "metadata": {}
}
```

---

# 32. Constraint Engine

约束分为三类。

## 32.1 Hard Constraint

不可违反：

```text
景点已关闭
交通无法到达
预约时间固定
航班 / 高铁时间固定
住宿入住限制
```

违反则计划判定 infeasible。

---

## 32.2 Soft Constraint

可以违反但产生 penalty：

```text
步行过多
行程太密
预算略高
兴趣匹配低
路线绕
```

---

## 32.3 Preference Constraint

因用户不同而不同：

```text
food_priority
photo_priority
museum_preference
morning_tolerance
walking_tolerance
night_activity
```

---

# 33. 行程评分函数

初步可使用加权评分：

```text
PlanScore =

w1 × PreferenceUtility
+ w2 × POICoverage
+ w3 × RouteEfficiency
+ w4 × TimeFeasibility
+ w5 × Diversity
- w6 × WalkingPenalty
- w7 × BudgetPenalty
- w8 × ChangeCost
```

后续可通过用户反馈学习权重。

---

# 34. Geo Planning

## 34.1 Geo Clustering

Wishlist 中地点先进行空间聚类。

可尝试：

- DBSCAN
- HDBSCAN
- K-Means（不优先）

更适合使用 DBSCAN，因为：

- 不需要预先指定 cluster 数
- 能识别离群 POI
- 基于空间距离较自然

---

## 34.2 Route Ordering

单日 POI 排序可以采用：

- Nearest Neighbor
- 2-opt
- TSP approximation
- OR-Tools

V1 推荐：

> Google OR-Tools / 自己实现 heuristic

避免让 LLM 决定地理顺序。

---

# 35. Replanning 算法设计

## 35.1 Event Schema

```json
{
  "event_id": "event_xxx",
  "trip_id": "trip_xxx",
  "type": "WEATHER_CHANGE",
  "timestamp": "...",
  "scope": "DAY_2",
  "severity": "HIGH",
  "payload": {}
}
```

事件类型：

```text
WEATHER_CHANGE
TRANSPORT_DELAY
POI_CLOSED
USER_CANCEL
USER_ADD
USER_FATIGUE
TIME_DELAY
RESERVATION_CHANGE
```

---

## 35.2 Impact Analysis

系统判断：

```text
Event
 ↓
Directly Affected Nodes
 ↓
Dependent Nodes
 ↓
Time Propagation
 ↓
Conflict Set
```

---

## 35.3 Local Search Window

为了避免整个行程重算，可以限定：

```text
Current Node
+
Next N Nodes
```

或者：

```text
Current Time → End of Day
```

作为 Local Replanning Window。

---

# 36. Memory Manager

Memory 分为四类：

```text
Working Memory
Episodic Memory
Semantic Preference
Reflection Memory
```

## Working Memory

当前旅行上下文。

## Episodic Memory

历史旅行事件。

## Semantic Preference

稳定偏好：

```text
喜欢夜市
不喜欢早起
步行上限
```

## Reflection Memory

从实际旅行总结出的策略：

```text
连续安排两个大型景点容易疲劳
午饭通常需要 90 分钟
用户喜欢留出夜间自由活动
```

---

# 37. Memory 更新策略

不是每次对话都写长期 Memory。

建议：

```text
Raw Event
 ↓
Candidate Memory
 ↓
Importance Scoring
 ↓
Deduplication
 ↓
Memory Consolidation
```

Memory Score：

```text
importance
× recurrence
× confidence
× future usefulness
```

---

# 38. Policy Engine

用于 Tool Risk Control。

例如：

```text
READ_ONLY
LOW_RISK_ACTION
CONFIRM_REQUIRED
DENY
```

V1 示例：

| 操作 | 策略 |
|---|---|
| 查询地图 | Allow |
| 查询天气 | Allow |
| 搜索 POI | Allow |
| 修改行程 | Allow |
| 删除用户收藏 | Confirm |
| 购买门票 | V1 Deny |
| 酒店支付 | V1 Deny |

为未来 Action Agent 留接口。

---

# 39. API 设计建议

REST 示例：

```text
POST   /trips
GET    /trips/{id}
PATCH  /trips/{id}

POST   /trips/{id}/wishlist
GET    /trips/{id}/wishlist

POST   /trips/{id}/plan/generate
POST   /trips/{id}/plan/validate
POST   /trips/{id}/plan/replan

POST   /trips/{id}/events
GET    /trips/{id}/events

POST   /content/parse
POST   /poi/search
```

---

# 40. 数据库 Schema 进一步细化

## users

```text
id
name
created_at
```

## trips

```text
id
user_id
destination
start_date
end_date
budget
pace_level
status
created_at
```

## trip_preferences

```text
trip_id
key
value
weight
source
```

## pois

```text
id
provider
provider_id
name
category
lat
lng
address
metadata
```

## wishlist_items

```text
id
trip_id
poi_id
source_type
source_url
interest_score
priority
notes
```

## trip_plan_nodes

```text
id
trip_id
day
node_type
poi_id
start_time
end_time
priority
flexibility
status
constraints
```

## trip_events

```text
id
trip_id
event_type
severity
payload
created_at
```

## memories

```text
id
user_id
memory_type
content
embedding
importance
confidence
created_at
updated_at
```

---

# 41. 前端交互重点

## 41.1 Map + Timeline 双向联动

点击地图 POI：

→ Timeline 自动定位。

点击 Timeline：

→ 地图聚焦 POI。

---

## 41.2 Plan Diff

Replanning 后不要只展示新计划。

展示：

```text
删除
修改
新增
保持
```

例如：

```text
- 14:00 人民公园
+ 14:00 四川博物院

= 18:00 春熙路
```

这是 Replanning Demo 的关键视觉点。

---

# 42. Demo 剧本

项目展示建议固定一个成都案例。

## Demo 1：收藏生成计划

导入：

- 8 个攻略链接
- 5 张截图
- 6 个手动收藏

展示：

```text
Raw Content
→ POI Extraction
→ Map
→ Geo Clustering
→ 4-day Plan
```

---

## Demo 2：Plan Check

故意放入：

- 周一闭馆 POI
- 两个距离过远地点
- 时间冲突

展示 Validator 自动识别。

---

## Demo 3：暴雨触发 Replan

Day 2 下午暴雨。

展示：

```text
Outdoor POI → affected
Indoor candidate search
Route recalculation
Plan Patch
```

并高亮变化节点。

---

## Demo 4：用户疲劳

用户：

> “今天累了。”

显示 pace_level 改变及计划局部缩减。

---

# 43. Evaluation Dataset 设计

可以自己构造：

```text
50–100 个 Trip Cases
```

覆盖：

- 成都
- 重庆
- 杭州
- 上海
- 北京
- 南京
- 西安

每个 Case 包含：

```text
用户偏好
Wishlist
时间
预算
已知约束
标准冲突
事件
```

---

# 44. Failure Case 分类

建议主动记录失败案例：

```text
Hallucinated POI
Wrong Opening Hours
Route Impossible
Too Dense
Preference Conflict
Weather Conflict
Tool Failure
Entity Linking Error
Replanning Cascade
```

每种 Failure 建立日志与评估。

---

# 45. Observability

Agent 系统一定要做 Trace。

每次 Run 记录：

```text
run_id
agent
input
tool_call
tool_result
latency
token_usage
error
retry
final_status
```

可以后续做一个：

**Agent Debug Dashboard**

查看：

```text
Planner
  ↓
maps.search
  ↓
weather.get
  ↓
validator
```

这也是很不错的工程亮点。

---

# 46. 6 周开发计划

## Week 1：基础设施与地图能力

目标：

- FastAPI
- PostgreSQL
- Trip CRUD
- 高德地图接入
- POI Search
- Route
- Weather
- 基础前端 Map

验收：

> 用户可以创建 Trip，并搜索、收藏地点。

---

## Week 2：Wishlist + Content Parsing

实现：

- Text Input
- Screenshot Input
- POI Extraction
- Entity Linking
- Wishlist Map
- Geo Clustering

验收：

> 用户能将非结构化攻略转换成结构化 Wishlist。

---

## Week 3：Planning Engine

实现：

- Preference Parser
- Constraint Model
- Geo Cluster
- Route Matrix
- Daily Planning
- Timeline UI

验收：

> 能生成一份具有地图可执行性的 3–5 日计划。

---

## Week 4：Validator + Agent Runtime + MCP

实现：

- Agent Loop
- MCP Gateway
- Planner Agent
- Research Agent
- Validator Agent
- Tool Trace

验收：

> Agent 可以自动调用地图、天气、搜索等工具并完成计划验证。

---

## Week 5：Dynamic Replanning + Memory

实现：

- Event System
- Impact Analysis
- Local Replan
- Plan Diff
- Preference Memory
- Episodic Memory

验收：

> 天气、疲劳、取消 POI 能触发局部计划修改。

---

## Week 6：Evaluation + Product Polish

实现：

- Evaluation Cases
- Metrics
- Ablation
- Agent Dashboard
- README
- 架构图
- Demo Video
- 简历描述

验收：

> 项目能够完整展示核心闭环，并有定量评估结果。

---

# 47. MVP 验收标准

V1 完成标准不是“功能很多”，而是以下闭环稳定可运行：

### Requirement 1

用户可以导入至少三种形式：

```text
Text
Screenshot
URL
```

---

### Requirement 2

系统可以将内容转换成结构化 POI Wishlist。

---

### Requirement 3

系统能根据真实地图距离生成 3–5 天计划。

---

### Requirement 4

计划能够验证：

```text
Route
Time
Weather
Opening Hours
Basic Reservation Risk
```

---

### Requirement 5

至少支持三种 Replanning Event：

```text
Weather
User Fatigue
POI Cancellation
```

---

### Requirement 6

Replanning 必须输出：

```text
Affected Nodes
Reason
Plan Diff
Updated Plan
```

---

### Requirement 7

用户反馈能改变后续 Traveler Profile。

---

# 48. 技术风险

## 风险 1：第三方数据不完整

解决：

- Provider Adapter
- 多源 fallback
- 用户确认
- Confidence 字段

---

## 风险 2：LLM Hallucination

原则：

> LLM 不直接创造事实。

地点、路线、营业时间尽量通过 Tool 验证。

---

## 风险 3：Agent 失控调用工具

使用：

- Tool Budget
- Max Steps
- Timeout
- Policy Engine
- Retry Limit

---

## 风险 4：范围过大

V1 坚持：

```text
国内
单城市 / 少量跨城市
规划
验证
重规划
```

不做交易闭环。

---

# 49. 后续研究 / 算法方向

如果后续希望项目带一点 Research 味道，可以进一步探索：

## 49.1 Preference Learning

根据用户行为更新规划权重。

---

## 49.2 Multi-objective Optimization

同时优化：

```text
Travel Time
User Utility
Budget
Fatigue
Diversity
Plan Stability
```

---

## 49.3 Learned Replanning Policy

比较：

```text
Rule-based
LLM-based
Hybrid
```

---

## 49.4 Multi-user Negotiation

多人旅行：

```text
Preference Aggregation
Fairness
Conflict Resolution
Subgrouping
```

---

# 50. 项目最终故事线

最终整个项目最好围绕一个核心问题展开：

> **旅行计划不是一次性生成任务，而是一个持续受到现实环境影响的动态决策过程。**

因此 TravelPilot 的技术主线应始终保持：

```text
Real-world Information
        ↓
Structured Context
        ↓
Constraint-aware Planning
        ↓
Executable Trip DAG
        ↓
Environment / User Event
        ↓
Impact Analysis
        ↓
Minimal-disruption Replanning
        ↓
Behavior Feedback
        ↓
Adaptive Traveler Memory
```

这也是项目最重要的差异化。
