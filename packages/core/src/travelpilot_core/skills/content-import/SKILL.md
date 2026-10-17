---
name: 攻略导入
version: 1.0.0
description: 从用户提供的文字或 OCR 文本中提取地点候选，并保留可核对的原文证据。
applies_to: content_import
requires: user_content
excludes: provider_unavailable
---

# 攻略导入

1. 只抽取原文明确出现的地点、餐馆或住宿名称，不根据常识补地点。
2. 每个候选保留一段最短的原文证据；证据必须逐字来自用户输入。
3. 把用户的评价作为建议文本，不把它转成已核验的地点事实。
4. 通过地图 Provider 搜索候选。无结果、多个匹配或 Provider 故障时标记待处理，不伪造坐标。
5. 地图候选只进入待确认列表。只有用户确认后才加入 Wishlist。

本 Skill 不授予任何工具权限，不可直接写入正式行程。
