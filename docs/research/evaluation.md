# 固定评测集

`tests/evaluation/cases/scenarios.json` 固定了八个场景。`test_cases.py` 使用纯规划器、局部重规划器和模拟 Provider 执行场景；这些结果是**离线逻辑测试**，不代表真实地图、天气或浏览器联调已通过。

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/evaluation -q
```

`metrics/metrics.py` 接收每次执行的实际记录，计算约束违规率、营业时间冲突率、Plan 与重规划成功率、Tool 成功率、改动节点数、Change Cost、耗时和 Token Cost。没有实际模型调用时，Token Cost 为零；不要将离线模拟统计写成线上质量指标。
