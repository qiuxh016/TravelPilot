# 重规划事件语义（首版）

本文描述当前代码的实际边界，并将算法基线和后续目标分开。核心规则位于 `packages/core/src/travelpilot_core/engines/impact_analysis/replan.py`；`USER_ADD` 由 API 层处理，不属于核心 `replan()` 的新增逻辑。

| 事件 | 输入 | 当前处理窗口与输出 | 保护规则 / 当前限制 |
|---|---|---|---|
| `WEATHER_CHANGE` | `effective_at`；`payload.condition` 可指定 `RAIN`、`WIND`、`HEAT`、`COLD`、`SNOW` 或 `STORM`，缺省按 `RAIN` | 仅当画像来源为 `USER`、`PROVIDER` 或 `EDITORIAL`，置信度至少 0.7，且地点声明为 `OUTDOOR` / `MIXED`、对应天气敏感度为 `HIGH` 时，才从受影响窗口移出；可信室内或低敏感地点保留；来源、置信度或属性未知时保留并警告。替换候选也必须有可信画像，并通过天气、营业时间、相邻路线时间、20 分钟换乘缓冲和每日 6 点容量检查 | `FIXED` / `RESERVED` 保留。不会根据地点类别猜测；低可信画像不能触发移除或替换。候选营业时间或路线数据未知时拒绝替换并写明原因。只在 8 个高优先级候选内搜索；地图故障时不伪造路线时间 |
| `USER_ADD` | 收藏中的 `poi_id`，事件日期和时间 | API 检查地点属于当前旅行收藏、避免重复，并按可用时段和路线时间尝试插入；生成待确认预览，确认后才创建新计划版本 | 核心 `replan()` 本身不添加节点。地图路线未知时不会伪造可达性；冲突预览不能确认 |
| `RESERVATION_CHANGE` | 目标 `poi_id`；可选 `start_time` / `end_time` | 目标在当前受影响窗口内时，缺时间则提示补充；有时间则改时段并设为 `FIXED` | 预约时间需在 09:00–20:00 且结束晚于开始；其他节点不会自动全局避让，冲突由 API 验证器发现 |
| `POI_CANCELLED` | 目标 `poi_id` | 仅移除受影响窗口内的目标地点 | 目标若早于事件时间、跨日或已固定则保留；被移除的时段不会自动搜索替代地点 |
| `USER_FATIGUE` | `effective_at` | 在受影响的可移动节点中，按优先级从低到高排序并移除前 `max(1, floor(n/2))` 个 | 固定/预约节点不计入候选；无显式疲劳等级、预计体力或“保留必去点”信号 |
| `TIME_DELAY` | `delay_min`，1–180 分钟 | 对同日且开始时间不早于事件时间的可移动节点统一顺延；结束超过 20:00 的节点删除 | 固定/预约节点保留。当前不传播前序延误到更晚的固定预约，也不自动检查地点间交通缓冲；API 可将冲突计划标记为 `PLAN_CONFLICT` |

除表中约束外，核心事件窗口目前统一为“事件当天、事件时刻及之后”；更早、其他日期的节点不变。核心层把 `FIXED` 和 `RESERVED` 当作不可修改状态。`effective_at` 的时区由 API Schema 要求，但核心以当地日期和时钟比较，不自行做时区换算。

## 预览与确认

`POST /trips/{trip_id}/replan-runs` 只保存 15 分钟有效的 `PREVIEWED` 工作流，返回来源计划版本、拟议版本、拟议状态、警告和 `PlanDiff`；不会创建 `TripPlan`、计划节点或版本差异记录，也不会修改旅行状态。`PLAN_CONFLICT` 和 `NEEDS_USER_INPUT` 预览不可确认。用户选择“确认采用”后，`POST /trips/{trip_id}/replan-runs/{run_id}/confirm` 会核对预览归属、来源计划版本、有效期、地点仍可用性和计划约束，再创建新版本。重复确认会返回同一正式版本；来源计划已变化时预览标记为过期状态并要求重新生成。

## 可重复基线

- 当前场景文件：`tests/evaluation/cases/replanning_baseline.json`；天气敏感规则版本为 `weather_aware_replan_v3`。v3 保留 v2 场景并增加低可信推断画像案例。历史 v1/v2 结果仍保留在 `tests/evaluation/results/rule_based_replan_v1.json` 与 `tests/evaluation/results/weather_aware_replan_v2.json`。
- 运行：`.venv\Scripts\python.exe -m tests.evaluation.replanning_baseline --output tests/evaluation/results/weather_aware_replan_v3.json`（从仓库根目录）。
- 基线采用稳定 UUID、固定事件时间和合成计划；JSON 结果中的差异、警告、变更数和 change cost 可直接比较。
- 基线记录当前规则行为，不是现实数据质量或算法效果结论。v1 将所有受影响的 `ATTRACTION` 地点都移除；v2 用显式画像区分室内、室外及未知属性；v3 再要求可信来源与至少 0.7 置信度，低可信度画像保留原计划并提示核验。
- 全局 POI 可保存可信 Provider / 编辑来源画像；用户手动画像写在 `wishlist_items`，按旅行收藏隔离。当前 AMap 适配器不产出室内外标签，不会把缺少的数据伪装成 Provider 已核验信息。
- `USER_ADD` 依赖数据库收藏查询、POI 元数据及 API 验证，不混入核心算法基线；需单独通过 API/数据库集成场景评估。
