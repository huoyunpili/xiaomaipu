from django.core.management.base import BaseCommand, CommandError

from app.accounts.models import User
from app.integrations.client import APIError
from app.integrations.models import Connection
from app.integrations.services import connect, execute_sync, queue_sync


class Command(BaseCommand):
    help = "验证授权店铺并同步平台待核对订单；不创建销售单或修改库存、资金。"

    def add_arguments(self, parser):
        parser.add_argument("--connect", action="store_true")
        parser.add_argument("--enable", action="store_true")

    def handle(self, *args, **options):
        try:
            if options["connect"]:
                actor = (
                    User.objects.filter(is_active=True, role=User.Role.ADMIN)
                    .order_by("date_joined")
                    .first()
                )
                if not actor:
                    raise CommandError("需要先创建管理员。")
                connection = connect(actor=actor)
            else:
                connection = Connection.objects.first()
            if not connection:
                raise CommandError("请先使用 --connect 验证授权。")
            run = queue_sync(connection)
            execute_sync(run.pk)
            run.refresh_from_db()
            if run.status != "DONE":
                raise CommandError(f"同步未完成：{run.status}")
            if options["enable"]:
                connection.enabled = True
                connection.save(update_fields=["enabled", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(f"同步完成：{run.count} 条平台记录；经营库存和资金未修改。")
            )
        except APIError as exc:
            raise CommandError(str(exc)) from None
