# 一起把鱼管家用得更顺手

欢迎小卖家测试自己的实际经营流程，也欢迎开发者帮助修复问题、完善文档和改进设计。本项目采用 Apache License 2.0；参与前请阅读 [LICENSE](LICENSE)、[行为准则](CODE_OF_CONDUCT.md) 和 [公开路线图](ROADMAP.md)。

## 报告问题

通过 GitHub Issues 选择“问题反馈”或“功能建议”。尽量描述：正在完成什么任务、具体操作步骤、实际结果、期望结果、使用的版本与部署方式。

金额问题请说明涉及的是客户实付、担保金额、预计回款、供货商追回款还是成交利润。可以用虚构订单和金额复现；截图请遮挡真实收货信息、订单号、成本及供货商名称。不要上传数据库、完整日志包、API 凭据或旧版生成的真实供货商链接。

如果问题涉及绕过登录、其他供货商可见订单、密钥暴露等，请阅读 SECURITY.md，不在公开 Issue 中发布可利用细节。

## 开发环境

普通用户只使用 Windows 安装包；以下步骤仅供开发和测试。需要 Python 3.12/3.13、[uv](https://docs.astral.sh/uv/) 和 Docker Desktop（用于隔离的 PostgreSQL、Redis）：

```powershell
git clone https://github.com/huoyunpili/xiaomaipu.git
cd xiaomaipu
uv sync --frozen
docker compose -f deployment/compose.yaml up -d --wait
uv run python manage.py migrate
uv run python manage.py bootstrap
uv run python manage.py runserver 127.0.0.1:8765
```

开发和测试不需要真实闲管家凭证。确需连接自己的测试店铺时，可将 `.env.example` 复制为被忽略的 `.env.local` 后填写；不要覆盖已有私有配置，不要在 Issue、日志或测试夹具中提交凭证和业务数据。

完整检查命令：

```powershell
uv run python scripts/check.py
```

## 提交改进

1. 较大改动先用 Issue 说明要解决的经营问题。
2. 在自己的分支开发；数据库结构变更提供迁移。
3. 用虚构数据测试，不能在自动化测试里发起真实平台发货、退款或消息发送。
4. 使用 `git commit -s` 添加 DCO `Signed-off-by`，表示你有权按照项目许可证提交贡献。
5. 运行 `uv run python scripts/check.py`；安装程序改动还要执行对应的 Windows 安装包检查。
6. PR 写清改动、验证结果和剩余限制；涉及页面时附脱敏截图。

不要提交 `.env.local`、`config.env`、`.local`、备份、真实订单导出或经营视频。不要添加固定公网访问令牌和个人电脑绝对路径。

请只提交自己有权提供的代码和素材；提交贡献表示你按照 [DCO](DCO) 作出声明，并允许项目按照 Apache License 2.0 整合、修改和发布贡献。第三方代码和素材必须标明来源及许可证，不兼容的内容不能提交。

## 维护方式

当前由作者阿栋（[@huoyunpili](https://github.com/huoyunpili)）维护产品方向和最终合并决定。项目会优先接受边界清楚、带测试、保护用户数据并解决真实经营问题的改动。较大的架构或业务变化请先通过 Issue 讨论，避免贡献者投入后方向不一致。
