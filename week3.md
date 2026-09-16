# TravelPilot Week 3：内容理解、Agent Runtime 与 MCP

目标：让文本和结构化收藏进入统一内容导入流程，并让 Agent 安全调用地图、搜索和旅行数据工具。

## 本周交付

用户可以粘贴攻略文本，提取地点和建议，确认 POI 候选，并让 Planner Agent 根据自然语言偏好生成规划请求。

本周不做自动购买、全网爬虫、复杂多人协商和完整长期记忆。

## Day 1：统一内容导入模型

创建：

    content_sources
    content_items
    extracted_entities
    entity_candidates

状态：

    RECEIVED
    PARSING
    NEEDS_CONFIRMATION
    IMPORTED
    FAILED

API：

    POST /api/v1/trips/{trip_id}/content
    GET  /api/v1/content/{content_id}
    GET  /api/v1/content/{content_id}/candidates
    POST /api/v1/content/{content_id}/confirm

## Day 2：文本解析 Agent

第一版只支持文本。

输入包含 content_type 和 text，输出地点名称、分类、建议、confidence。模型输出必须经过 Pydantic Schema，不确定地点不能直接入库。

## Day 3：Entity Linking 和用户确认

流程：

    文本地点
      → Provider 搜索候选
      → 计算候选匹配度
      → 用户确认
      → 写入 POI/Wishlist

前端创建：

    apps/web/features/content-import/
    apps/web/app/trips/[tripId]/import/

显示原文、抽取结果、候选 POI、置信度和确认按钮。

## Day 4：Tool Port 和 MCP Adapter

定义：

    SearchPlacesTool
    GetPlaceDetailTool
    GetRouteTool
    GetWeatherTool
    GetTripContextTool

创建：

    packages/core/src/travelpilot_core/ports/tools.py
    packages/infrastructure/src/travelpilot_infra/mcp/

Agent 依赖 Tool Port，MCP 只是具体适配方式。每个 Tool 都要有 Schema、timeout、retry、错误码和 trace。

## Day 5：Agent Runtime

创建：

    packages/core/src/travelpilot_core/agents/
    packages/core/src/travelpilot_core/workflows/

统一 AgentResult：

    status
    output
    tool_calls
    errors
    next_action

最小循环：

    Task
      → Build Context
      → LLM Decision
      → Tool Call
      → Observation
      → Finish / Retry / Ask User

限制最大步骤数、Token Budget、Tool Timeout、Retry Limit，并禁止未知 Tool。

## Day 6：Planner Agent 和 Trace

Planner Agent 负责理解偏好、读取 Trip/Wishlist、调用确定性 Planner、请求 Validator 和生成解释。

它不能编造 POI、决定真实路线、绕过 Validator 或直接覆盖已发布 Plan。

记录：

    agent_runs
    tool_calls
    workflow_runs

每次记录 run_id、agent、trip_id、tool、latency、token usage、error 和 status。

## Day 7：回归和验收

    .\.venv\Scripts\python.exe -m pytest -q
    pnpm --dir apps/web build

Week 3 必须满足：

- 文本可解析为地点候选；
- 模糊地点需要用户确认；
- 确认后才进入 Wishlist；
- Planner Agent 可以调用 Tool；
- Agent 不能绕过确定性 Planner 和 Validator；
- Tool 调用有超时、重试和日志；
- Agent 失败不会破坏 Trip 数据。

