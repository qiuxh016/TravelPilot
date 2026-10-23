# 多用户首发边界与数据隔离设计

## 产品边界

- 首发形态：封闭试用的多用户响应式 Web MVP。
- 登录标识：规范化为小写的邮箱；凭据为密码。
- 一个用户只能修改自己创建的 Trip。Trip 默认只有创建者可读取；创建者可给已接受的好友授予单 Trip 只读权限，仅通过 `/api/v1/shared/trips` 读取旅行摘要及可选的当前计划。
- Wishlist、Plan/PlanChange 的修改接口、攻略导入及候选、事件、反馈、私有记忆、Agent/Workflow/Tool trace 和后台任务仍由 Trip 所有者独占。好友关系不会自动产生 Trip 权限。
- TravelMoment 默认 `PRIVATE`；好友只能看到作者明确设置为 `FRIENDS` 的动态。图片走受认证 API，每次读取都会重新检查动态受众和好友关系。帖子关联 Trip 不向好友公开该 Trip 内容。
- 当前支持好友申请/接受/移除/屏蔽，以及好友移除、屏蔽或旅行所有者撤销时撤销分享。尚不支持协作成员、组织/团队或管理员角色。
- 搜索地图、路线与地点属于公共 Provider 能力；持久化 Trip 与关联内容为私有数据。
- 账户导出为 JSON；账户删除立即撤销会话，并级联删除账户旅行及其私有派生记录。POI 是地点目录数据，不视为账户私有记录，不因单个账户删除而级联删除。

## 身份与会话

- 邮箱密码注册和登录。密码使用 scrypt 哈希；数据库仅存哈希。密码最少 12 个字符。注册后需通过 24 小时有效的一次性邮件链接验证邮箱；密码重置链接 1 小时有效且只能使用一次，成功后撤销该账户所有会话。
- 浏览器使用 12 小时 HttpOnly、SameSite=Lax 会话 Cookie，写请求校验双提交 CSRF token；命令行/API 客户端可继续使用 Bearer token。数据库仅存会话 token 的 SHA-256 摘要。
- 可通过 `REGISTRATION_INVITE_CODE` 限制封闭试用注册；生产模式未设置邀请码时 API 拒绝启动。开发模式留空时开放本地注册。
- 登录失败按规范化邮箱与来源 IP 组合计数；默认 15 分钟内失败 5 次后锁定 15 分钟，可通过 `LOGIN_MAX_ATTEMPTS`、`LOGIN_WINDOW_MINUTES`、`LOGIN_LOCKOUT_MINUTES` 调整。登出、过期或删号后会话失效。
- 注册、登录分别按 IP 每分钟 30 次限流；验证邮件/密码重置申请共用按 IP 每小时 30 次限流；实际邮件发送共享滚动每小时 100 封预算。限流键用 HMAC 处理 IP；生产环境必须配置至少 32 字符的 `AUTH_RATE_LIMIT_HASH_SECRET`，所有 API 实例使用相同值。另有按账户/邮件用途 60 秒冷却。Redis 不可用时公开认证入口返回 503，不绕过限流。可用 `PUBLIC_AUTH_IP_LIMIT_PER_MINUTE`、`EMAIL_REQUEST_IP_LIMIT_PER_HOUR`、`EMAIL_SEND_LIMIT_PER_HOUR` 和 `EMAIL_ACTION_COOLDOWN_SECONDS` 配置。
- API 默认要求认证；健康检查、注册/登录、邮箱验证和密码重置入口公开，重置申请对外使用统一响应以降低账户枚举。跨账户资源统一按 404 处理。
- 开发环境使用本机调试邮箱取回链接；生产环境必须配置 SMTP。未配置邮箱送达能力时不可部署生产模式。
- 邮件环境变量：`WEB_BASE_URL`、`SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM_EMAIL`、`SMTP_USE_TLS`。SMTP 凭据只放在部署环境，不提交到仓库。
- 首发不承诺 MFA、账户合并、SSO、协作编辑或组织管理。
- 好友圈实施进度和内容分享授权模型见 [`../product/social-travel-memory-design.md`](../product/social-travel-memory-design.md)。专用分享 API 仅开放被授权的摘要/计划只读访问；其他 Trip 路由仍须保留所有者依赖。好友动态、图片、评论和反应均按帖子受众与当前好友关系鉴权；双账户脚本覆盖私密/好友可见、媒体权限、撤销、Trip 分享范围及删除清理。

## 数据迁移

- 新账户通过 `users.id` 作为 Trip 所有者；私有查询先按用户筛选 Trip，再访问其派生资源。只读分享查询必须同时核对有效 grant 和当前已接受好友关系。
- 升级前已存在的 Trip 归入禁用的迁移占位用户，不向任何新账户暴露。需要管理员在受信任环境中核实归属后再转移；禁止默认把历史数据开放给首个注册用户。
- 账户删除沿 `users -> trips / moments -> dependent rows` 数据库级联执行，并删除本地媒体原图与缩略图。Provider 地点属于共享目录而保留；用户手动创建且不再被任何 Wishlist/Plan 引用的自定义 POI 会清除。

## 封闭试用上线门槛

当前实现适合邀请码控制的受控试用，不代表可无条件开放互联网注册。对外开放前仍需完成 HTTPS 与生产域名/CORS 配置、可信反向代理 IP 配置、告警平台接入、生产备份恢复演练，以及隐私政策和数据保留期限确认。API 已对 429 与 5xx 输出不含用户内容的结构化安全事件日志，但尚未接入告警平台。当前限流依赖 Redis，部署时须确认 Uvicorn 仅信任实际代理地址的转发头；不要信任任意客户端提供的 `X-Forwarded-For`。

反向代理配置要求和建议的数据生命周期见 [`data-lifecycle.md`](data-lifecycle.md) 与 [`../operations/production-and-integrations.md`](../operations/production-and-integrations.md)。

## 验收

- 无 token 的受保护 API 返回 401。
- 用户 A 创建的私有 Trip 及任一派生资源，用户 B 无法读、写、删或触发任务；响应为 404。仅当 A 明确授权时，B 可经专用只读接口读取获准范围；B 仍不能修改任何 Trip 数据。
- 导出只包含当前用户 Trip 及关联记录。
- 删除用户后，该用户会话、Trip 和 Trip 派生数据清空，旧 token 不能再使用；被其他旅行引用的地点仍保留。
