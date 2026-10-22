# AI 旅行顾问未见多轮任务评测：v4 诊断记录

日期：2026-10-22  
模型：`deepseek-flash`（`api.deepseek.com`）  
代码提交：`63709a0ff275a64c2498a7b15a61672c5c41481d`（工作区当时有未提交变更）  
任务集：`advisor_multiturn_holdout_v4`，20 个任务、40 轮用户输入  
原始文件 SHA-256：`2d6721e1d088e4e17c8a0980531837714c508fd5f83456f1e06d05c8ea46d326`

## 评测范围与安全边界

比较了 B2（生产 AgentRuntime）与 B3（同一运行时加验证与有限恢复），并另跑了离线脚本策略 B1 诊断。B2/B3 使用真实 DeepSeek 动作选择；工具结果来自本地合成只读夹具。没有调用高德、天气 API 或旅行数据库，也没有正式行程写入。

实际使用 81 次模型请求、4,474 个输出 token；上限为 120 次请求、单次 128 token、总计 15,360 token。未触及请求或输出预算。运行 provenance 记录在本地忽略结果 `tests/evaluation/results/advisor_unseen_multiturn_pilot_v2.json` 中；原始结果没有提交。该次运行使用旧 runner，报告内 `evaluation_id` 静态写成了 v1；runner 现在已改为从数据版本生成正确 ID。

任务 ID 与十套历史任务集无重叠，40 条规范化输入也没有精确重叠。该检查不能证明语义独立，也不能证明对任意用户任务的泛化能力。

## 结果

| 方法 | 动作匹配 | 全任务匹配 | 无效模型动作 | 硬安全违规 |
|---|---:|---:|---:|---:|
| B1 离线规则策略 | 34/40（85%） | 不适用 | 不适用 | 0 |
| B2 Agent Loop | 29/40（72.5%） | 12/20（60%） | 9 | 0 |
| B3 Agent Loop + 验证/恢复 | 29/40（72.5%） | 12/20（60%） | 8 | 0 |

**这些数字不能解释为 B1/B2/B3 公平对照。** B1 是启发式脚本，不是产品旧版固定流程；因此只作独立离线诊断，不与真实模型分数作优劣比较。

## 数据夹具缺陷与可用范围

三个路线任务没有给 `get_trip_route` 所需的 `known_places` ID 证据。生产参数校验因此拒绝了对应的模型动作。这是评测夹具缺陷，不能算作模型的路线理解错误，也不能把这些轮次作为有效路线质量结果。整任务排除后，B2、B3 均为 **28/34 动作、12/17 全任务**。该排除后数字仍是探索性信号，不是预先声明的主指标。

排除上述路线任务后，B2 有 4 次、B3 有 3 次结构化动作解析失败；其中多次输出达到本试验单轮 128 token 上限，另有 JSON 内容解析错误。B3 仅观察到 1 次有限恢复决策，未提升剩余有效子集的任务完成数。完整原始输出正文没有保存，所以不能对这些文本失败进一步归因。

新加的前置校验会在花费模型请求前拒绝缺少两个 `known_places` ID 的路线评测案例。由于 v4 的提示已被展示且实际运行，该集合现已曝光；不得修补后再声称是盲测，也不得用于后续提示优化后的盲测成绩。

## 结论与剩余门槛

- v4 暴露了实用性问题：多轮任务中的无效结构化输出较多，且开放咨询可能触及输出上限。下一轮需要对输出格式和简洁回复做通用改进，再以全新、证据完整的任务集复测。
- 本次没有产品级自然语言回复留存，不能评价回复是否清楚、有帮助或简洁；也没有完成人工盲评。
- P0 真实模型泛化验收**尚未通过**：缺少真正匹配的生产固定流程 B1、有效完整的全新任务集和独立盲评。
- 已使用 81/120 个授权请求。剩余额度 39 次不足以对 20 个双轮任务补跑完整 B1/B2/B3。后续应先冻结修订后的方法与全新任务，再申请/确认独立额度；不能把 v4 暴露集重跑当作补齐。

## 本地复现

```powershell
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_multiturn_contracts `
  --dataset tests\evaluation\cases\advisor_multiturn_holdout_v4.json `
  --output tests\evaluation\results\advisor_multiturn_holdout_v4_validation.json
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_unseen_multiturn_eval `
  --run-live --dataset tests\evaluation\cases\advisor_multiturn_holdout_v4.json `
  --request-cap 120 --output-token-cap 128 --total-output-token-cap 15360 `
  --output tests\evaluation\results\advisor_unseen_multiturn_pilot_v2.json
```

实时命令会再次发起真实模型请求。不要重跑已曝光的 v4 来获取盲测结论。



