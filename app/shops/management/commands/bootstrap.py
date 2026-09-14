from django.core.management.base import BaseCommand
from django.db import transaction

from app.shops.models import SalesChannel, Shop

CHANNELS = [
    ("XIANYU", "闲鱼"),
    ("WECHAT", "微信"),
    ("REFERRAL", "熟客转介绍"),
    ("OFFLINE", "线下"),
    ("OTHER_PLATFORM", "其他平台"),
    ("OTHER", "其他"),
]


class Command(BaseCommand):
    help = "幂等初始化单店铺及销售渠道，不创建账号、不改写已有配置。"

    @transaction.atomic
    def handle(self, *args, **options):
        Shop.objects.get_or_create(is_active=True, defaults={"name": "我的小卖铺"})
        for code, name in CHANNELS:
            SalesChannel.objects.get_or_create(
                code=code, defaults={"name": name, "is_platform": code == "XIANYU"}
            )
        self.stdout.write(self.style.SUCCESS("店铺与 6 个默认渠道已就绪。"))
