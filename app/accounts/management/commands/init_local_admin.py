from django.core.management import call_command
from django.core.management.base import BaseCommand

from app.accounts.models import User


class Command(BaseCommand):
    help = "Create the first local administrator interactively; never overwrites an existing one."

    def handle(self, *args, **options):
        if (
            User.objects.filter(is_active=True).filter(role=User.Role.ADMIN).exists()
            or User.objects.filter(is_active=True, is_superuser=True).exists()
        ):
            self.stdout.write(self.style.SUCCESS("管理员已经存在，初始化未改动账号。"))
            return
        self.stdout.write("请创建你自己的管理员账号。密码不会写入配置或日志。")
        call_command("createsuperuser", interactive=True)
