# P3 邀请码封闭试用运维边界

## 已落地的运行配置

- `docker-compose.prod.yml` 运行 Web、API、Worker、PostgreSQL、Redis 和 Caddy；数据库与 Redis 只在 Compose 网络内，公网仅开放 Caddy 的 80/443。
- Caddy 为单一域名自动申请 HTTPS，并把 `/api/*` 与 readiness 路由转发给 API；Web/API 共用浏览器来源，登录 Cookie 不需跨域。
- 生产 API 启动时执行 Alembic migration。API readiness 检查数据库、迁移版本和 Redis；Worker readiness 检查心跳。
- 生产配置验证邀请码、HTTPS 来源、SMTP、S3 存储和至少 32 字符限流哈希密钥。真实邮件和对象存储仍需由运营者配置供应商凭据。
- `scripts/backup-postgres.ps1` 导出 PostgreSQL 自定义格式备份；`scripts/restore-drill.ps1` 在断网、无端口暴露的临时容器中恢复并核对迁移版本及用户/旅行行数。照片存储在独立对象存储，数据库备份不包含照片。

## 建议的初始运维目标（上线前需确认并配置）

- RPO：最多 24 小时数据；建议每日一次数据库备份，备份副本最长保留 30 天；异账号/异区域副本至少每周一次。
- RTO：建议 4 小时内恢复数据库、API 和 Web；先恢复 DB，再部署兼容版本，再验证 readiness、账户登录、旅行读取和私有照片读取。
- 备份加密后存放在生产主机之外；对象存储启用版本控制/生命周期或跨区域复制。定期恢复演练并记录实际耗时。不要把仅存在同一主机的副本视为灾备。
- 当前脚本为手动 PostgreSQL 备份与恢复演练工具，未配置自动计划、备份保留删除、异地复制或告警；正式部署前须接入并演练这些运营流程。
- 上线前执行账号注册、过期/重发验证邮件、密码重置、媒体上传/跨账号拒绝/删除、账户删除，以及 AMap/Open-Meteo/LLM/SMTP/Worker 故障降级检查。
- 账户删除会清理数据库记录并请求删除原图与缩略图；现存备份按上述最长保留期限自然过期，需在隐私说明中明确这一边界。

## 上线仍需外部配置和签收

1. 单一区域的 Linux 主机/容器平台、域名 DNS 指向和 80/443 入站规则；HTTPS 证书由 Caddy 自动管理。
2. SMTP 事务邮件供应商、验证通过的发件域和凭据；发送失败/退信告警由供应商侧配置。
3. 私有 S3 bucket、区域、最小权限凭据、备份/生命周期策略；生产媒体访问仍通过 API 所有权检查，不暴露 bucket 公读。
4. 异区域加密备份目的地、自动计划和告警接收人。当前备份/恢复脚本提供手动演练，不会自行调度或监控异地复制。
5. 运营支持联系人，以及数据用途、分享可见范围、照片处理、备份保留和账户删除说明的最终文案。

## 启动与演练

复制 `.env.production.example` 为未跟踪的 `.env.production`，替换全部占位值；数据库和 Redis 密码建议使用至少 32 位随机十六进制字符串，避免 URL 保留字符；`SITE_DOMAIN` DNS 必须解析到部署主机。然后执行：

```powershell
docker compose --env-file .env.production -f docker-compose.prod.yml config --quiet
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
$siteDomain = (Get-Content .env.production | Where-Object { $_ -match '^SITE_DOMAIN=' } | Select-Object -First 1) -replace '^SITE_DOMAIN='
Invoke-RestMethod "https://$siteDomain/ready"
```

备份和隔离恢复演练：

```powershell
.\scripts\backup-postgres.ps1
.\scripts\restore-drill.ps1 -BackupFile .data\backups\travelpilot-YYYYMMDD-HHMMSS.dump
```

备份文件可能含用户数据；演练结束后应按保留策略管理或安全删除本地副本。
