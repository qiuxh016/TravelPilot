# Agent 本地评测结果（2026-10-21）

## 评测范围与来源

本轮只运行本地确定性评测和本地产品服务套件，没有调用 DeepSeek、其他外部模型、高德地图或 Open-Meteo。评测对应源码 revision `d1d42df7037bb7250a5a6a2c758f634e1d3739c7`；运行时工作树有未提交修改，因此结果通过评测器记录的源文件 SHA-256 绑定到当时的本地实现，不能说成该 commit 的干净复现。原始 JSON 结果保存在本机 `%TEMP%\travelpilot-w5-eval\`，不包含在仓库提交中。

| 套件 | 数据集 / 覆盖 | 数据集 SHA-256（评测器记录） | 运行方式 |
|---|---|---|---|
| 完整任务 B1/B2/B3 | 40 个已有旅行约束任务；16 个场景族 | `a8ee82eb0ef789a639199f623cbd7adcbcbdf8fe146985e0c890d5bf13240f7b` | 确定性地图/营业时间/故障 fixtures + scripted policies |
| 多轮脚本对照 | 60 案例、129 轮；涵盖攻略导入、跨域、目的地发现、餐饮、行程、住宿、记忆、恢复、交通和天气 | `a964d0bb8e3be36fdbc41f82a736e58ad9cf3af0ee0d009708c7d9cd4a6b5af9` | 无 LLM、无外部 Provider 的 scripted policies |
| 多轮生产 Runtime 机制 | 同一 60 案例/129 轮；实际走受限 Agent Loop 与 AgentRuntime 的 76 个 Agent-owned 决策轮 | 同上 | 本地合成工具与 scripted policy |
| 认证产品服务套件 | 18 个服务场景；22 轮 HTTP 交互 | 由各场景 JSON 记录的来源指纹 | 本地模型桩、Fake Maps、合成天气和路由故障 |

## 结果

### 40 案例完整任务对照

| 组别 | 预期动作匹配 | 预期结果匹配 | 硬约束违规 | 工具调用 |
|---|---:|---:|---:|---:|
| B1 固定顾问回放 | 20/40 | 29/40 | 11 | 30 |
| B2 Agent Loop | 36/40 | 35/40 | 1 | 39 |
| B3 Verifier + Recovery | 40/40 | 40/40 | 0 | 39 |

该集合是已用于开发的场景，不是 holdout。B1 是固定流程回放，B2/B3 是脚本策略；相同确定性规划器负责计划可行性。因此这里只能说明这些受控案例中的契约差异，不能证明真实模型决策质量或对新旅行任务的泛化。

### 60 案例多轮脚本对照

| 组别 | 动作匹配 | 状态匹配 | 模拟工具调用 | 策略违规 |
|---|---:|---:|---:|---:|
| B1 固定策略 | 51/129（39.53%） | 73/129（56.59%） | 50 | 0 |
| B2 观察策略 | 86/129（66.67%） | 88/129（68.22%） | 47 | 0 |
| B3 守卫策略 | 86/129（66.67%） | 88/129（68.22%） | 47 | 0 |

在 76 个由 Agent 决策拥有的轮次中，B2/B3 均为 55/76（72.37%）。该多轮策略针对已有套件迭代过，仍属于 in-sample 回放。B2 与 B3 得分相同，当前证据不支持“Verifier + Recovery 提升动作质量”；其价值须结合下方运行时安全机制与具体故障案例描述。

### 多轮生产 Runtime 机制

实际受限执行环节处理了 76 个 Agent 决策、50 次本地夹具工具调用和 23 次用户追问。Loop 状态为成功 48、等待用户 23、受控失败 5；未授权工具调用 0、allowlist 拒绝 0、评测器失败 0。该项验证工具边界和状态机制，不是对动作正确率、自然语言质量或用户效用的评分。

### 认证产品服务套件

18/18 场景通过，共 22 轮；所有 18 个临时账户已删除，所有场景都阻止了正式行程写入。覆盖多轮排程与改稿、路线/天气、餐饮分类、餐饮加景点、住宿、自由目的地发现、开放旅行问题、天气建议、跨城交通能力边界，以及路线或天气 Provider 故障。每个场景的断言均记录在本机原始 JSON 中。

这 22 轮本地 API 请求记录的端到端延迟均值为 **309.5 ms**、中位数 **317.4 ms**、范围 **122.9–512.1 ms**。评测器的本地 token 记账字段合计输入/输出各 **1,080**；响应来源为 MODEL 6 轮、TEMPLATE 15 轮、未标来源 1 轮。此延迟只反映本机 Stub/Fake 服务和当前开发环境；记账值不是外部模型供应商账单或可比较的模型推理成本。

## 指标边界与结论

- 可报告：冻结脚本集中的动作/状态匹配、硬约束违规、工具调用次数；受限 Runtime 的授权/allowlist 边界；产品 API 场景通过率与临时账户清理。
- 未测得：本轮真实 LLM 的供应商 token 成本与模型延迟、真人偏好/效用、未见任务泛化、真实地图与天气准确率。上面的本地请求延迟和 token 记账值仅用于本地回归观察；脚本工具计数不是 Provider 成本，产品套件使用的本地模型桩不代表模型质量。
- 40 案例的 B3 零硬约束违规仅针对其 fixtures；多轮集 B2/B3 相同分数说明不能声称 B3 在多轮决策上优于 B2。
- 本报告满足本计划的“本地评测闭环”门槛。新增真实模型复测需另行冻结方案与预算；真实模型质量不作为本次计划完成结论。

## 复现命令

在仓库根目录的 PowerShell 中执行；这些命令会把结果写入临时目录，不覆盖同名已有结果：

```powershell
$outDir = Join-Path $env:TEMP 'travelpilot-w5-eval-rerun'
New-Item -ItemType Directory -Force $outDir | Out-Null
python -m tests.evaluation.advisor_full_task_eval --dataset tests/evaluation/cases/advisor_existing_trip_agent_v3.json --output (Join-Path $outDir 'full-task.json')
python -m tests.evaluation.advisor_multiturn_task_eval --dataset tests/evaluation/cases/advisor_multiturn_v5.json --output (Join-Path $outDir 'multiturn-scripted.json')
python -m tests.evaluation.advisor_multiturn_runtime_eval --dataset tests/evaluation/cases/advisor_multiturn_v5.json --output (Join-Path $outDir 'multiturn-runtime.json')
python scripts/verify_advisor_product_local_suite.py --output (Join-Path $outDir 'product-local.json') --per-turn-token-cap 512 --session-token-cap 1024
python -m pytest tests/evaluation/test_advisor_full_task_eval.py tests/evaluation/test_advisor_multiturn_runtime_eval.py tests/evaluation/test_advisor_multiturn_contracts.py -q
```

产品服务套件需要本地 PostgreSQL/Redis 和项目的测试环境配置；评测器强制使用本地 Stub/Fake Provider，且清理临时账户。若要复现端到端 API 结果，应确认 Docker 数据服务可用并检查运行输出中的 `provider_mode` 与清理状态。
