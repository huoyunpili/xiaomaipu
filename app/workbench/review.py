"""Owner review supplements projections without rewriting platform facts."""

import hashlib
import json

from django import forms
from django.db import transaction
from django.utils import timezone

from app.accounts.policies import require_admin
from app.audit.models import AuditEvent
from app.common.business import BusinessError, record_event
from app.integrations.models import PlatformOrder

from .models import Trade


class ReviewForm(forms.Form):
    fingerprint = forms.CharField(widget=forms.HiddenInput)
    result = forms.ChoiceField(
        label="核对结果",
        choices=[
            ("", "请选择你在平台核实的结果"),
            ("full", "已确认买家全额退款完成"),
            ("pending", "部分退款、仍在处理中或尚无法确认（保留待核对）"),
        ],
    )
    amount = forms.DecimalField(
        label="平台实际已退款金额（元）",
        min_value=0,
        max_value=10000000000,
        decimal_places=2,
        required=False,
        help_text="按闲鱼退款详情填写。未查到请留空，不要照抄接口中的 0。确认全额退款时必填。",
    )
    basis = forms.CharField(
        label="核对依据 / 尚待处理的问题",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="例如：已查看闲鱼退款详情，显示全额退款成功；或：实际为部分退款，待进一步处理。",
    )
    confirmed = forms.BooleanField(
        label="我已核实对应订单、退款状态和金额",
        help_text="此确认不会登记供应商货款已收回。",
    )


def fingerprint(row):
    from .services import effective_order_data

    data = effective_order_data(row)
    facts = {
        k: data.get(k)
        for k in (
            "order_status",
            "refund_status",
            "pay_amount",
            "pay_time",
            "refund_amount",
            "refund_time",
            "apply_amount",
            "cancel_time",
            "consign_time",
            "confirm_time",
        )
    }
    facts["quantity"] = data.get("goods", {}).get("quantity")
    facts["conflicts"] = row.contract_issues
    facts["refund_conflict"] = row.refund_snapshot.get("_needs_review")
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def full_refund_allowed(row):
    from .services import effective_order_data, stamp

    data = effective_order_data(row)
    paid = data.get("pay_amount")
    amount = data.get("refund_amount")
    return bool(
        type(paid) is int
        and paid > 0
        and type(data.get("order_status")) is int
        and data.get("order_status") in (11, 12, 21, 22, 23, 24)
        and type(data.get("refund_status")) is int
        and (data.get("order_status") == 23 or data.get("refund_status") == 5)
        and data.get("refund_status") in (0, 5)
        and stamp(data.get("refund_time"))
        and (amount is None or type(amount) is int and amount in (0, paid))
        and not row.refund_snapshot.get("_needs_review")
        and not any("同一更新时间" in issue for issue in row.contract_issues)
    )


def active_resolution(trade, row):
    resolution = trade.review_resolution
    if resolution and resolution.get("fingerprint") == fingerprint(row):
        return resolution
    return {}


def context(trade):
    from .services import effective_order_data, stamp

    row = trade.platform
    data = effective_order_data(row) if row else {}
    problems = []
    paid, amount = data.get("pay_amount"), data.get("refund_amount")
    if row:
        if amount is None:
            problems.append("接口没有提供已退款金额。")
        elif amount == 0 and (data.get("order_status") == 23 or stamp(data.get("refund_time"))):
            problems.append(
                "接口记录了退款成功状态或时间，但已退款金额为 0；不能据此认定没有退款。"
            )
        elif amount not in (0, paid):
            problems.append("接口退款金额与实付金额不同，可能是部分退款或数据冲突。")
        if not stamp(data.get("refund_time")):
            problems.append("接口缺少有效的退款完成时间。")
        if data.get("order_status") == 23 and data.get("refund_status") == 0:
            problems.append("订单状态为退款成功，但退款状态字段仍为无退款，两个字段不一致。")
    order_labels: dict[object, str] = {
        11: "未付款",
        12: "待发货",
        21: "已发货",
        22: "已完成",
        23: "退款成功",
        24: "已关闭",
    }
    refund_labels: dict[object, str] = {
        0: "无退款",
        1: "退款处理中",
        2: "退款处理中",
        3: "退款处理中",
        5: "退款成功",
        6: "退款被拒绝",
        8: "退款处理中",
    }
    return {
        "trade": trade,
        "order_status": order_labels.get(data.get("order_status"), "未知 / 未提供"),
        "refund_status": refund_labels.get(data.get("refund_status"), "需核对平台"),
        "paid": paid if type(paid) is int else None,
        "refund_amount": amount if type(amount) is int else None,
        "refund_time": stamp(data.get("refund_time")),
        "problems": problems,
        "can_confirm_full": bool(row and full_refund_allowed(row)),
        "resolution_active": bool(row and active_resolution(trade, row)),
        "history": AuditEvent.objects.filter(
            object_id=str(trade.pk),
            action__in=["workbench.review_recorded", "workbench.review_revoked"],
        ).select_related("actor")[:20],
    }


@transaction.atomic
def save_review(*, trade, actor, expected, result, amount, basis):
    from .services import effective_order_data, project

    require_admin(actor)
    if not trade.platform_id:
        raise BusinessError("此订单未关联平台记录，请先补齐订单来源，不能直接确认退款。")
    row = (
        PlatformOrder.objects.select_for_update()
        .select_related("connection__shop")
        .get(pk=trade.platform_id)
    )
    trade = Trade.objects.select_for_update().get(pk=trade.pk)
    if expected != fingerprint(row):
        raise BusinessError("平台信息在你查看后发生了变化，请重新查看本页再确认。此次结果未保存。")
    if result not in ("full", "pending") or not basis.strip():
        raise BusinessError("请选择核对结果并填写核对依据。")
    if amount is not None and (type(amount) is not int or not 0 <= amount <= 10**12):
        raise BusinessError("请填写有效的实际退款金额。")
    if result == "full":
        if not full_refund_allowed(row):
            raise BusinessError(
                "平台尚未提供一致的退款成功状态和时间，或已提供不同的退款金额。请先重新同步；仍有冲突时记录待处理结果。"
            )
        if amount != effective_order_data(row).get("pay_amount"):
            raise BusinessError("全额退款的实际金额必须等于订单实付。部分退款请选择保留待核对。")
    resolution = {
        "fingerprint": expected,
        "result": result,
        "amount": amount,
        "basis": basis.strip(),
    }
    if all(trade.review_resolution.get(k) == v for k, v in resolution.items()):
        return trade
    resolution["checked_at"] = timezone.now().isoformat()
    trade.review_resolution = resolution
    trade.refund_success_confirmed_at = None
    trade.save(update_fields=["review_resolution", "refund_success_confirmed_at"])
    record_event(actor, "workbench.review_recorded", trade, **resolution)
    return project(row)


@transaction.atomic
def revoke_review(*, trade, actor, expected):
    from .services import project

    require_admin(actor)
    if not trade.platform_id:
        raise BusinessError("此订单没有平台记录。")
    row = (
        PlatformOrder.objects.select_for_update()
        .select_related("connection__shop")
        .get(pk=trade.platform_id)
    )
    trade = Trade.objects.select_for_update().get(pk=trade.pk)
    if expected != fingerprint(row):
        raise BusinessError("平台信息已变化，请刷新页面后再撤销。")
    if trade.review_resolution:
        previous = trade.review_resolution
        trade.review_resolution = {}
        trade.refund_success_confirmed_at = None
        trade.save(update_fields=["review_resolution", "refund_success_confirmed_at"])
        record_event(actor, "workbench.review_revoked", trade, previous=previous)
    return project(row)
