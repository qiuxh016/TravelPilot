# TravelPilot Week 4：动态重规划、Memory、评测与交付

目标：完成 TravelPilot 的核心差异化闭环：现实事件触发局部重规划，用户反馈进入 Traveler Memory，并形成可演示、可测试、可评估的 MVP。

## 最终演示

    导入收藏
      → 生成 Wishlist
      → 生成 Plan
      → 验证计划
      → 发生天气/疲劳/取消事件
      → 局部重规划
      → 展示 Plan Diff
      → 记录反馈和记忆

## Day 1：事件模型和 Trip 状态

事件类型：

    WEATHER_CHANGE
    USER_FATIGUE
    POI_CANCELLED
    TIME_DELAY
    USER_ADD
    RESERVATION_CHANGE

创建：

    trip_events
    workflow_runs

Trip 状态：

    DRAFT
    PLANNING
    VALIDATING
    READY
    ACTIVE
    COMPLETED
    PLAN_CONFLICT
    NEEDS_USER_INPUT

## Day 2：Impact Analysis 和局部窗口

创建：

    packages/core/src/travelpilot_core/engines/impact_analysis/
    packages/core/src/travelpilot_core/workflows/replanning/

流程：

    Event
      → 找到直接受影响节点
      → 找到时间依赖节点
      → 计算局部窗口
      → 冻结未受影响节点

第一版窗口限定为“当前时间到当天结束”。固定预约和交通节点不可移动。

## Day 3：Replanner 和 Plan Diff

API：

    POST /api/v1/trips/{trip_id}/events
    POST /api/v1/trips/{trip_id}/replan-runs
    GET  /api/v1/trips/{trip_id}/plans/{plan_id}/diff

计划版本：

    Plan v1
      → Event
      → Plan v2

Diff 操作：

    KEEP
    ADD
    REMOVE
    MOVE
    UPDATE

每个变更必须有 node stable key、reason code、before、after 和 change cost。

## Day 4：Traveler Memory

Memory 类型：

    Working Memory
    Semantic Preference
    Episodic Memory
    Reflection Memory

创建：

    memories
    user_feedback

更新流程：

    Raw Feedback
      → Candidate Memory
      → Importance/Confidence
      → 去重
      → 写入长期 Memory

不要每条对话都写长期记忆，允许用户确认、修改和删除记忆。

## Day 5：评测数据集和指标

创建：

    tests/evaluation/cases/
    tests/evaluation/metrics/
    docs/research/

第一批案例：

- 成都 4 日轻松旅行；
- 雨天室外 POI 替换；
- 用户疲劳减少行程；
- POI 临时取消；
- 时间冲突；
- 营业时间冲突；
- 路线过长；
- Provider 失败。

指标：

    Constraint Violation Rate
    Opening-hour Conflict Rate
    Plan Success Rate
    Changed Node Count
    Change Cost
    Replanning Success Rate
    Tool Success Rate
    Latency
    Token Cost

## Day 6：可观测性、Demo 和文档

记录 API 请求、workflow、agent run、tool call、数据库错误、重试、计划版本和 Plan Diff。

固定 Demo：

    1. 导入文本和地点
    2. 确认候选 POI
    3. 生成成都 4 日计划
    4. Validator 发现冲突
    5. 模拟暴雨
    6. 触发局部重规划
    7. 展示 Plan Diff
    8. 用户反馈“今天很累”
    9. 更新 Traveler Memory

## Day 7：最终回归和发布

    docker compose up -d postgres redis
    .\.venv\Scripts\alembic.exe upgrade head
    .\.venv\Scripts\python.exe -m compileall -q apps packages
    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\ruff.exe check .
    pnpm --dir apps/web build
    pnpm --dir apps/web exec playwright test

## Week 4 最终验收

产品闭环：

- 用户可以导入内容；
- 系统可以生成 Wishlist；
- 用户可以确认 POI；
- 系统可以生成计划；
- 计划可以验证真实约束；
- 事件可以触发局部重规划；
- 前端可以展示变更 Diff；
- 用户反馈可以更新 Traveler Profile。

工程闭环：

- API、Worker、数据库和前端可本地启动；
- 有 migration、测试和固定评测数据；
- Agent Tool 调用可追踪；
- 关键错误可诊断；
- README 可以指导新成员启动；
- 代码已推送到 origin/main。

四周内不做自动购票、自动支付、大规模爬虫、全球 Provider、复杂多人协商和生产级 Kubernetes 部署。

