# 行程约束协商本地验收 v2

日期：2026-10-22

## 本次覆盖

扩展了原来的七个确定性场景，现共 9 项：无冲突、单个重叠、两个独立重叠、两个固定预约直接冲突、固定预约与可移动活动冲突、用餐窗口、营业时间、缺少路线证据，以及活动落在旅行日期之外。每个协商器返回的备选项还会独立重新送入求解器验证。

API 集成边界另由 `apps/api/tests/test_constraint_negotiation.py` 验证：草案版本过期、上下文版本过期、路线证据缺失/失败/过期、候选方案应用和用户计划写入边界均有回归用例。

## 结果

| 指标 | 结果 |
|---|---:|
| 场景数 | 9 |
| 有已知可修复冲突 | 5 |
| 仅报告冲突的基线提出修复 | 0/5 |
| 只接受单项变更的基线修复 | 4/5（80%） |
| 有界协商器提出可行修复 | 5/5（100%） |
| 两组独立冲突的联合修复 | 通过 |
| 固定/必需活动被改动或候选方案未通过复核 | 0 |
| 超出旅行日期的活动被接受 | 否，正确拒绝 |
| 本机求解耗时 | 均值 0.989 ms；最大 4.427 ms |

上述延迟是本机单次合成输入的测量，不代表线上延迟。warning-only 与单项变更是诊断基线，不是产品旧版 B1。评测未调用模型、地图、天气、数据库或真实行程写入服务，也不证明真实路况或用户偏好质量。

## 复现

```powershell
.\.venv\Scripts\python.exe -m tests.evaluation.constraint_negotiation_eval
.\.venv\Scripts\python.exe -m pytest tests\evaluation\test_constraint_negotiation_eval.py apps\api\tests\test_constraint_negotiation.py apps\api\tests\test_advisor_route_validation.py apps\api\tests\test_advisor_scheduling.py -q
```
