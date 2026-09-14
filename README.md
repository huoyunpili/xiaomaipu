# 小卖铺经营后台

2026-09-11 更新：当前为 **0.4.0 本地 API 接入查验版**，新增授权验证、后台增量同步、平台订单核对与销售草稿关联、推送接收、失败重试和登录限流。接入步骤、实测结果与上线边界见 [API 接入与发布查验](docs/API接入与发布查验-0.4.0.md)。下文 0.3.0 为原经营功能基线。

0.3.0 经营功能基线：已包含自由货况与库存、供应商报价及采购、缺货采购关联、供应商直发、分批发货与剩余数量关闭、收退款及利润、客户档案与风险提醒、私有视频取证、CSV/XLSX 后台导入、渠道报表和库存市场风险。详细查验步骤与边界见 [本地统一查验说明](docs/本地统一查验说明-0.3.0.md)。

当前本机预览为 http://127.0.0.1:8765 ，继续使用原账号；启动说明中的 8000 端口用于新环境自行启动。

## 本轮可试用流程

1. 进入“货盘 → 新增商品”，一起填写名称、货况、库存数量和单件成本，点击“保存商品和库存”；暂时没货可填 0，其他描述仍选填。
2. 同款增加另一组货时，在商品详情“新增一件/一组货”填写该组数量和实际货况，不同货况分别管理。
3. 进入“订单 → 新增订单”，先选商品，再勾选货况卡片、填写数量/单价和买家/渠道；页面显示成交总额。也可在实物组点击“卖这组货”预选对应货物。保存后核对，草稿不锁库存。
4. 确认订单后锁定现有库存；缺货时补货并再次“补锁库存”。闲鱼渠道需人工核对买家付款，确认后仍为平台托管。
5. 点击“发货 / 交付”，填写本次发货数量、物流或选择自提/当面交付，确认本次实际费用。剩余数量可继续备货发货，各次运单分别保留；全部发出后确认快递履约完成。
6. 分次登记实际到账；履约完成且净收款等于调整后应收时才确认利润，超收款不自动成为利润。
7. 取消释放未出库库存；已收到的款项单独退回。退款与应收减免分开记录。
8. 退货实际收到后登记待检；管理员验收可售后重新入库，报废不增加可售库存。退货不会自动触发现金退款。

进货时只填单件进货成本，按实际记账金额录入，不单独填写采购运费。例如成交 200 元、进货成本 105 元、履约费用 10 元，最终利润为 85 元。若只收到 50 元，则仍待收 150 元，已实现利润显示待确认。

## 本地启动（Windows PowerShell）

新增采购流程：进入“采购”，先建立供应商，再直接新增采购或按报价采购；确认下单后分次收货入库，实际付款与退款分别记录。缺货订单可点击“为缺货采购”，到货后核对实际货况并为原订单备货。详情见 [采购与供应商执行记录](docs/M4-采购与供应商执行记录.md) 和 [缺货采购关联记录](docs/M4-缺货采购关联执行记录.md)。已有环境更新后先执行 `python manage.py migrate`。

需要 Python 3.12 或 3.13、uv、Docker Compose v2。命令均在项目根目录执行。

```powershell
uv sync --frozen
docker compose -f deployment/compose.yaml up -d --wait
uv run python manage.py migrate
uv run python manage.py bootstrap
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

打开 http://127.0.0.1:8000 。管理员密码通过 Django 命令交互设置，没有默认账号密码。再次运行 bootstrap 不覆盖已有资料。不需接通闲管家 API 即可启动。

若 uv 安装在本项目虚拟环境内，可把 `uv` 替换为 `.\.venv\Scripts\uv.exe`，把 `uv run python` 替换为 `.\.venv\Scripts\python.exe`。

默认 PostgreSQL：`127.0.0.1:55432`，开发库 `seller`，账号 `seller`，密码 `seller-local-only`；仅供本机开发。Redis 使用 `127.0.0.1:56379`。需要更改时复制 `.env.example` 为 `.env.local`，修改相应字段；Compose 使用 `--env-file .env.local` 保持配置一致。

## 验证

后台导入需要独立 Worker；每日公开信息读取需要 Beat。开发机可在另外两个终端运行：

```powershell
.\.venv\Scripts\python.exe -m celery -A app.config.celery worker --pool=solo --concurrency=1 --loglevel=INFO
.\.venv\Scripts\python.exe -m celery -A app.config.celery beat --schedule=.local/celerybeat-schedule --loglevel=INFO
```

私有视频默认存放 `.local/private-media`，不提供公开静态链接。手机扫码需要手机可访问的站点地址，可用 `PUBLIC_BASE_URL` 配置实际局域网/HTTPS 地址，并同步配置允许的主机；当前 localhost 预览仅本机可访问。

本地 Docker 数据库与视频备份、独立恢复校验：

```powershell
.\.venv\Scripts\python.exe manage.py backup_local --verify
```

备份写入 `.local/backups`，恢复到新建的校验数据库，核对后删除校验库，不覆盖现有库。正式远端备份与生产灾备仍需部署环境配置。

首次验证先执行 `uv run playwright install chromium`。本机已安装 Chrome 时也可设置 `$env:PLAYWRIGHT_CHANNEL='chrome'` 使用已有浏览器。

```powershell
uv run python scripts/check.py
```

依次执行 Ruff 检查与格式校验、mypy、Django 系统检查、迁移漂移检查、pytest。测试使用独立 `test_seller` 数据库并自动清理；本地账号须具备创建测试数据库的权限。并发测试使用真实 PostgreSQL 连接。

首次迁移必须先执行，因为自定义用户从初始迁移开始使用。已有环境升级同样执行 `migrate`；`makemigrations --check --dry-run` 确保模型修改不会漏掉迁移文件。

## 结构

- `app/accounts`：UUID 用户与管理员/操作员角色。
- `app/shops`：单店铺约束、默认渠道及设置服务。
- `app/common`：事务幂等、Outbox 存储、请求标识和健康检查。
- `app/audit`：业务操作记录，无编辑删除入口。
- `app/integrations`：闲管家只读客户端、增量同步、平台快照、销售草稿关联及签名推送。
- `app/catalog`、`app/inventory`：自由货况、实物组、余额及库存流水。
- `app/orders`、`app/finance`：订单动作、分配快照、收支及利润历史、退货验收。
- `tests`：权限、重复提交、并发、回滚、转义与日志检查。
- `deployment`：开发数据库及生产 Web/Worker/Beat/Caddy 骨架。

`/health/` 检查 Web 与数据库；Worker 可通过 `celery -A app.config.celery inspect ping` 检查。后台导入与每日 RSS 任务已启用，未配置/未启用的信息源不会联网。通用 Outbox 保留审计事件，外部业务消息发送未接入。

## 生产部署骨架

生产云环境尚未部署，本地恢复演练已执行，生产备份与灰度验收仍需在实际部署环境执行。生产配置须提供 `DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`、`POSTGRES_PASSWORD`、`SITE_DOMAIN` 和 HTTPS 的 `CSRF_TRUSTED_ORIGINS`，并设置 `APP_IMAGE=xianyu-seller:0.4.0`。私有视频使用持久化卷 `private_media`。

```powershell
docker compose --env-file .env.production -f deployment/compose.yaml -f deployment/compose.production.yaml build web
docker compose --env-file .env.production -f deployment/compose.yaml -f deployment/compose.production.yaml up -d postgres redis
docker compose --env-file .env.production -f deployment/compose.yaml -f deployment/compose.production.yaml run --rm web python manage.py check --deploy
docker compose --env-file .env.production -f deployment/compose.yaml -f deployment/compose.production.yaml run --rm web python manage.py migrate
docker compose --env-file .env.production -f deployment/compose.yaml -f deployment/compose.production.yaml run --rm web python manage.py bootstrap
docker compose --env-file .env.production -f deployment/compose.yaml -f deployment/compose.production.yaml run --rm web python manage.py createsuperuser
```

启动前将镜像 `/srv/app/staticfiles/` 导出到项目 `staticfiles/` 供 Caddy 只读使用；执行 `up -d` 启动服务。数据库与 Redis 不映射生产端口，Web 只通过 Caddy 访问。登录限流已加入，首次外网使用前仍须配置运维备份并完成部署环境验收。

回滚优先恢复前一应用镜像，不自动反向迁移或删除数据。0.2.0 增加独立业务表及订单规格快照，不改写已有用户和店铺；已有业务数据时不运行迁移至 zero，不执行 `docker compose down -v`。

## API 验证

自动化质量检查清空 API 凭据，不触发真实接口。本地已按用户授权启用每 5 分钟只读同步，管理员可在“平台同步”关闭。`sync_xgj --connect` 可验证并读取平台待核对记录；独立探针仍不连接经营数据库。生产接入须配置 `XGJ_APP_KEY` 和 `XGJ_APP_SECRET`。


## 钱货迁移前的只读检查

```powershell
.\.venv\Scripts\python.exe manage.py reconcile_business_data --check
```

在只读一致快照中核对现有销售收退款、采购付款/收退记录和库存余额；有差额时非零退出，不自动修复。历史事实缺失单独列为迁移缺口，不代表完整业务审计。T0 基线、备份及后续迁移范围见 [T0 执行记录](docs/T0-基线与迁移准备.md)。
