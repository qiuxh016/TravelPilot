# TravelPilot 本地质量验收

日期：2026-10-20

代码提交基线：`c3b26f7`（阶段 3）
结论：阶段 0–4 的本地验收完成。结果只适用于仓库内冻结案例、测试替身和本机浏览器；不能据此声称真实模型或真实地图/天气服务达到相同质量。

## 运行范围

| 验收项 | 本地结果 | 数据与边界 |
|---|---:|---|
| API 与离线评测回归 | 909 passed，1 条既有 Starlette/httpx 弃用警告 | `pytest apps/api/tests tests/evaluation -q`；无失败 |
| 攻略文本、OCR、结构与证据回归 | 24 passed | `test_content_extraction_quality.py` 与 `test_content_skill_trace.py`；覆盖文本/图片 OCR、分类、原文证据、跨段完整性、失败段重试和确认写入边界；模型响应使用桩或本地规则 |
| 顾问端到端本地产品任务 | 18/18 passed | 本地模型桩、Fake Maps、合成天气和故障 Provider；18 个临时账户均删除，正式行程写入均被阻止 |
| 固定工作流与 Agent 对照 | 40 个任务 | 见下表；确定性脚本策略，不是 LLM 质量评测 |
| 多轮 holdout 合同 | 16 案例、35 轮通过结构与覆盖校验 | `advisor_multiturn_holdout_v2` 首轮评分子集为 5 案例、10 轮；子集是脚本策略诊断，不代表泛化质量 |
| AgentRuntime 多轮执行 | 10/10 个 Agent 决策轮执行；allowlist 拒绝 0、未授权工具调用 0 | 本地 fixture 工具与确定性策略；不调用模型或外部服务 |
| 顾问浏览器交互 | 确认选项桌面 1 passed、移动端 1 passed；额度交互桌面/移动端各 1 passed | Playwright API mock；新建会话入口另以 API mock 检查，390px 视口无横向溢出 |

## B1/B2/B3 产品任务对照

冻结集包含 40 个受控旅行规划任务，按同一确定性规划器和注入的 Provider 状态执行。B1 重放固定顾问流程；B2 使用观察驱动的 Agent Loop；B3 增加验证与有限恢复。

| 版本 | 预期动作匹配 | 预期结果匹配 | 硬约束违规 | 工具调用 |
|---|---:|---:|---:|---:|
| B1 固定流程 | 20/40（50%） | 29/40（72.5%） | 11 | 30 |
| B2 Agent Loop | 36/40（90%） | 35/40（87.5%） | 1 | 39 |
| B3 Loop + Verifier/Recovery | 40/40（100%） | 40/40（100%） | 0 | 39 |

这些是脚本策略对冻结场景的符合度结果，不是模型智能水平的估计。策略与产品状态机共享仓库代码，且案例规模和分布有限；结果只证明实现覆盖了这些约束情形。另一个新冻结的五案例/十轮多轮 holdout 得到 B1 8/10、B2 4/10、B3 4/10 动作匹配。这组较低的结果也保留为限制证据：当前泛化到不同多轮表达的能力尚未由本地确定性策略证明。

## 攻略导入检查范围

固定梅州攻略和变体覆盖游览地点、餐馆、住宿、日期/时段、开放时间、购票、交通路线和原文证据；图片通过本地 OCR 后进入文本解析流程。回归确保原文证据必须可定位、分类可以确认前修改、跨段输入不静默截断，且失败段可以单独重试。旅行级事实和地点级事实在确认时分开处理。

这 24 项是代码行为与故障处理回归，不构成对真实模型抽取精确率/召回率的估计。真实模型大攻略抽取准确率仍待另行测量；本轮计划按用户指定，以本地评测完成作为结束条件。

## 评测中修正的一项判定

首次运行 18 场景套件时，17 项通过、`unsupported-intercity-weather` 被判失败。检查场景结果后，铁路查询不可用和不自动改驾车的说明确实呈现在产品的“等待你补充”结构化卡片，但评测器只检查消息正文。评测器现在把正文与可见待确认问题一起评分；新增回归覆盖卡片中的限制说明和用户未授权时不调用路线工具。重新运行后 18/18 通过。产品安全行为未因改动而放宽。

## 可重复命令

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest apps/api/tests tests/evaluation -q
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_content_extraction_quality.py apps/api/tests/test_content_skill_trace.py -q
.\.venv\Scripts\python.exe scripts/verify_advisor_product_local_suite.py --output "$env:TEMP\TravelPilot-advisor-product-local.json"
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_full_task_eval --output "$env:TEMP\TravelPilot-advisor-full-task.json"
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_multiturn_contracts --output "$env:TEMP\TravelPilot-advisor-contracts.json"
```

在 `apps/web` 目录运行桌面与移动端顾问交互验收：

```powershell
pnpm exec playwright test e2e/advisor-confirmation-options.spec.ts e2e/advisor-budget.spec.ts
```

## 完成本轮的范围

- 阶段 0A–0C：长攻略分段解析、有限输出预算、分段合并、失败保留与局部重试。
- 阶段 1：结构化攻略事实、证据校验、可编辑确认和历史续办。
- 阶段 2：Skill 版本评估与个性化记忆安全边界。
- 阶段 3：顾问简洁多轮交互、结构化选项、上下文失效保护、计划预览/确认工作台。
- 阶段 4：冻结本地评测、桌面/移动浏览器验收与结果报告。

外部生产试用、持续可用性、配额和运维按既定范围留待后续，不阻止本地计划结束。未执行真实模型、真实地图或真实天气调用；本报告不包含密钥、用户原文或模型原始回复。
