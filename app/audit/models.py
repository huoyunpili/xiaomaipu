from django.conf import settings
from django.db import models

from app.common.models import BaseModel


class AuditEvent(BaseModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100, blank=True)
    request_id = models.CharField(max_length=36, blank=True)
    details = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)

    @property
    def action_label(self):
        return {
            "shop.updated": "更新店铺资料",
            "product.saved": "保存商品资料",
            "stock.received": "商品入库",
            "stock.adjusted": "盘点调整",
            "stock.description_updated": "修改实物货况",
            "order.created": "创建订单",
            "order.confirm": "确认订单与锁库存",
            "order.cancel": "取消订单",
            "order.ship": "发货或交付",
            "order.complete": "确认履约完成",
            "money.receipt": "登记收款",
            "money.refund": "登记退款或减免",
            "money.fee": "补录费用",
            "return.received": "收到退货待检",
            "return.inspected": "退货验收",
            "xgj.connected": "验证闲管家授权",
            "xgj.sync_start_confirmed": "确认自动同步起始日",
            "xgj.enable": "开启闲管家自动同步",
            "xgj.disable": "关闭闲管家自动同步",
            "xgj.order_linked": "关联平台订单",
            "xgj.reviewed": "核对平台变化",
            "xgj.refund_checked": "查询平台售后事实",
            "account.updated": "修改自己的账号",
            "supplier.shipping_rejected": "平台拒绝发货请求",
        }.get(self.action, "业务操作")

    class Meta:
        ordering = ["-occurred_at"]
