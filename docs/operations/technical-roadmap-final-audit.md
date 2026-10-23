# TravelPilot 技术路线验收审计

审计日期：2026-10-17  
范围：`计划.md` 中的约束感知重规划、受约束 Agent、Skill 适用性和可重复实验。  
边界：区分本地/合成验证、真实 Provider 观察和生产环境验收。

## 验收结论

| 计划项 | 状态 | 证据与范围 |
|---|---|---|
| 约束感知的最小改动重规划 | 本地完成 | 天气画像、硬约束过滤、有界联合搜索、PlanDiff/change cost、固定节点保护、拒绝原因和节点级搜索诊断已接入。基线、替换、联合搜索、天气规则消融、8/16/32 候选压力场景及 USER_ADD 局部插入都有确定性评测；USER_ADD 在路线未知时拒绝插入。数据为合成场景，不表示真实行程质量或全局最优。 |
| 目标敏感性与偏好校准 | 工具完成，数据待收 | 有默认权重敏感性评测和按旅行分组的成对反馈校准器。校准要求至少 5 个旅行和 10 个比较会话，并做 leave-one-trip-out；合成演示只验证实现，尚无真实反馈，因此没有应用新权重。 |
| 不可行诊断 | 本地完成 | 各受影响节点记录候选检查数、硬约束拒绝计数、替换结果和原因码；路线矩阵不可用时能诊断 `ROUTE_TIME_UNKNOWN`，不伪造路线。 |
| 受约束 Agent 决策闭环 | 本地完成 | 有限 observe/act/verify、工具白名单、步数/尝试/token 限制、脱敏 Trace、checkpoint、幂等恢复、确定性排程和用户确认边界。上下文与路线矩阵有确定性校验；矩阵失败/被拒绝时回退本地估算并说明。 |
| Skill 适用性 | 路由及决策记录本地验收完成，真实效果有初步配对证据 | Skill 有任务前提、证据要求、排除条件、版本固定和拒绝原因；4 个确定性用例及本地数据库流程覆盖适用/拒绝，并持久化脱敏原因。v7 六次均通过 Schema，三个合成案例全部成对成功；两个正向案例的开关双方 precision/recall 均为 1.0，未观察到质量差异。Skill 总 token 比基线多 354（963 vs 1,317）。样本过小且内容简单，不构成普遍质量结论。 |
| 可重复实验 | 本地完成 | `experiment_reproducibility_v18.json` 记录 10 个合成套件在两个独立进程各运行一次，固定 Python/hash seed=`20261017`，进程 ID 不同，模型和地图凭据置空；结果摘要一致。输入摘要散列 Planner 决策/API、工具协议与适配器、Python 依赖锁文件、抽取器、Skill 定义和评测运行器。 |

## 本轮复核证据

- 全量 Python 测试：`193 passed`，1 条既有 Starlette/httpx 弃用警告。恢复测试已隔离本机 `.env` 的模型配置，避免该回归测试访问真实服务。
- 全量 Ruff：通过。
- Web TypeScript：通过。
- Alembic 漂移检查：`No new upgrade operations detected.`
- `scripts/verify_agent_recovery.py`：真实本地 PostgreSQL 中断恢复、单次计划提交和幂等重放通过；LLM 与地图调用关闭。
- `scripts/verify_multi_user.py`：双用户资源隔离、分享权限、导出、账户删除和级联清理通过；LLM 关闭，地图使用 Fake Provider。
- `scripts/verify_advisor.py`：地图候选核验、用户确认后写入、旧计划保护、版本差异、导出删除通过；LLM 关闭，地图使用 Fake Provider。
- `tests/evaluation/results/experiment_reproducibility_v9.json`：`stable_outputs_match=true`，包含 4 个 Skill 适用性场景，全部符合预期；Provider 模式为 `SYNTHETIC_DETERMINISTIC`、生成 token 为 0，不含真实模型质量结论。
- `tests/evaluation/results/experiment_reproducibility_v11.json`：10 个套件各重复两次，`stable_outputs_match=true`；7 个 USER_ADD 场景与 4 个 Skill 场景全部符合预期。Provider 模式为 `SYNTHETIC_DETERMINISTIC`，生成 token 为 0。
- `tests/evaluation/results/experiment_reproducibility_v12.json`：10 个套件重复结果稳定；报告输入摘要包含 Skills 路由/定义、抽取器和 live ablation 运行器代码。
- `tests/evaluation/results/experiment_reproducibility_v13.json`：10 个套件重复结果稳定；7 个 USER_ADD 与 4 个 Skill 场景预期通过，Provider 为合成确定性模式且生成 token 为 0。
- `tests/evaluation/results/experiment_reproducibility_v14.json`：10 个套件重复结果稳定；新增决策链和依赖清单散列，Provider 为合成确定性模式且生成 token 为 0。
- `tests/evaluation/results/experiment_reproducibility_v15.json`：10 个套件重复结果稳定；摘要包含最新抽取提示与 thinking 模式实现源码。没有使用真实模型。
- `tests/evaluation/results/experiment_reproducibility_v16.json`：10 个套件重复结果稳定；摘要包含 confidence 类型提示和最新 live ablation 实现源码。没有使用真实模型。
- `tests/evaluation/results/experiment_reproducibility_v17.json`：10 个套件重复结果稳定；输入摘要含配对 token 成本计算代码。没有使用真实模型。
- `tests/evaluation/results/experiment_reproducibility_v18.json`：两个独立子进程分别运行全部 10 个套件，固定 seed，进程 ID 不同，输出摘要一致；外部模型/地图凭据已在子进程置空。
- `apps/api/tests/test_replan_api_integration.py`：验证 USER_ADD 保留固定预约，路线成功时选择可行时段；地图故障和路线时间过长时不新增，并对旅行日期越界返回 422。
- `scripts/verify_user_add.py`：本地 PostgreSQL 持久化演练通过，确认 `+08:00` 事件从 timestamptz 往返后仍按 11:00 安排，固定的 14:00 预约未移动，新增节点和诊断已写入数据库；地图矩阵由假 Provider 注入。
- `tests/evaluation/results/content_import_skill_live_ablation_v4.json`：用户批准的 6 次真实模型请求，每次 cap 160，实际生成 token 合计 155；5 次返回空内容并触发 `json_invalid`，1 次成功。无完整成功配对，不能比较 Skill 质量。
- `tests/evaluation/results/content_import_skill_live_ablation_v5.json`：用户批准的 6 次真实模型请求，每次 cap 160，实际生成 token 合计 960；6 次均为 HTTP 200、`finish_reason=length`、completion tokens 达到上限，响应正文为空。无完整成功配对；证据显示输出预算截断与空 JSON 同时发生。
- `tests/evaluation/results/content_import_skill_live_ablation_v6.json`：用户批准的 6 次真实模型请求，关闭思考模式，每次 cap 160，实际生成 token 合计 417；4 次 Schema 成功、2 次因 confidence 类型校验失败。只有无地点名的负向案例配对成功，正向案例无成功配对。报告的 evaluation ID 已修正为 v6。
- `tests/evaluation/results/content_import_skill_live_ablation_v7.json`：用户批准的 6 次真实模型请求，每次 cap 160，关闭思考模式，实际生成 422 输出 token；3/3 对照配对成功。两个正向案例均为两地点全召回、precision/recall 1.0、证据匹配 2/2，开关结果一致；negative case 双方无地点幻觉。Skill 总 token 为 1,317，基线为 963。该小样本不能证明广泛效果。
- 抽取失败诊断现在额外保留脱敏 HTTP 状态、完成原因与 token 用量，并有空响应及敏感信息不泄漏测试；旧 v4 报告不含这些新增字段。
- Live ablation 的成功记录现包含 HTTP 状态、finish reason 和延迟；失败报告也会把 `finish_reason=length` 分类为 `MODEL_OUTPUT_TRUNCATED` 并提升 HTTP 状态字段。纯模拟测试验证六次记录、实际 token 合计与截断分类，不访问外部模型。
- 对截断和格式的缓解已在真实调用中观察：v6 关闭思考模式后输出未达上限但 Skill 正向样例的 confidence 格式错误；v7 加入数值格式要求后六次响应均通过 Schema。结论仅适用于本轮模型、配置与三条短样例。

## 尚未验收的外部证据

1. **真实 Skill 效果：**v7 完成 3 组成对真实模型调用；两个短正向案例开关双方均精确抽取全部地点，Skill 没有测得质量增益，同时比基线多用 354 个总 token。样本规模与难度不足以作总体结论；后续扩展案例仍需新的调用预算授权。另有一次来自未隔离恢复测试的意外模型请求，已修复隔离；该请求不是对照样本，也不能用于质量判断。
2. **真实偏好校准：**需采集经同意、去标识化且按旅行分组的候选选择；达到最少样本量并通过分组验证后才可讨论默认权重变更。
3. **生产环境：**多进程并发恢复、生产域名与 SMTP/S3、自动备份、异地副本、告警及实际 RPO/RTO 演练需要部署平台、凭据和运营接收人；本地代码测试不能证明这些项目已就绪。

## 结论

本地实现与离线技术验收已完成，包含规划、约束 Agent、Skill 路由和确定性复核。整个计划仍有真实模型效果、真实用户偏好和生产运维三类外部证据缺口；在这些证据取得前，不应把它们标记为已验证或把合成指标当作真实质量。
