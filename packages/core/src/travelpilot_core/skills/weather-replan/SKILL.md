---
name: 天气变化重排
version: 1.0.0
description: 根据已获取的天气数据，为受影响日期提出局部替代草案。
applies_to: weather_replan
requires: verified_weather
excludes: simulated_weather
---

# 天气变化重排

1. 天气只能使用天气 Provider 返回的值，并保留来源、更新时间和预报日期；模拟天气不得当作真实预报。
2. 只调整受影响的日期，保留其他日期和用户锁定的行程节点。
3. 室内地点必须经过地图 Provider 核验；营业时间未知时明确标记未知。
4. 把交通变化、被移除地点和新增地点放入局部差异，并说明无法验证的部分。
5. 生成待确认草案；在用户确认前不更新正式 Plan 或 Trip。

本 Skill 不授予任何工具权限，不可直接写入正式行程。
