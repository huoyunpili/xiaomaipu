from django.utils import timezone

from app.accounts.policies import require_admin, require_operator
from app.common.business import BusinessError, whole
from app.common.services import execute_once
from app.orders.models import SalesOrder
from app.orders.services import event, refresh_profit

from .models import CustomerPaymentFact, MoneyEntry


def record_money(
    *,
    actor,
    submission_key,
    order_id,
    kind,
    amount_fen,
    version,
    reduction_fen=0,
    reason="",
    request_id="",
    occurred_at=None,
    account_type="UNKNOWN",
):
    require_operator(actor)
    account_type = account_type or "UNKNOWN"
    validate_cash_metadata(occurred_at, account_type)
    whole(amount_fen, "金额")
    whole(reduction_fen, "应收减免")
    if kind not in ("RECEIPT", "REFUND", "FEE") or not reason.strip():
        raise BusinessError("请选择收款、退款或费用，并填写实际到账依据或原因。")
    if kind != "REFUND" and (not amount_fen or reduction_fen):
        raise BusinessError("收款或费用金额须大于零，且不能带应收减免。")
    if kind == "REFUND" and not (amount_fen or reduction_fen):
        raise BusinessError("退款金额和应收减免不能同时为零。")

    def action():
        order = SalesOrder.objects.select_for_update().get(pk=order_id)
        if version != order.version:
            raise BusinessError("订单金额已变化，请刷新核对后重新提交。")
        if order.status == SalesOrder.Status.DRAFT:
            raise BusinessError("请先确认订单，再登记实际收款或费用。")
        if order.status == SalesOrder.Status.COMPLETED and kind in ("REFUND", "FEE"):
            require_admin(actor)
        if kind == "RECEIPT":
            if order.status == SalesOrder.Status.CANCELLED:
                raise BusinessError("已取消订单不能继续收款。")
            order.received_fen += amount_fen
        elif kind == "REFUND":
            if amount_fen > order.net_received_fen:
                raise BusinessError(
                    "退款不能超过已实际收到的净款项。平台托管退款不能冒充卖家现金退款。"
                )
            if reduction_fen > order.adjusted_due_fen:
                raise BusinessError("应收减免不能超过当前应收。取消订单的应收已归零。")
            order.refunded_fen += amount_fen
            order.amount_reduction_fen += reduction_fen
        else:
            order.fees_fen += amount_fen
        MoneyEntry.objects.create(
            order=order,
            actor=actor,
            direction="IN" if kind == "RECEIPT" else "OUT",
            occurred_at=occurred_at,
            time_quality="EXACT" if occurred_at else "UNKNOWN",
            account_type=account_type,
            kind=kind,
            amount_fen=amount_fen,
            reduction_fen=reduction_fen,
            reason=reason,
        )
        refresh_profit(order, f"money.{kind.lower()}")
        event(order, actor, f"money.{kind.lower()}", reason, request_id)
        return {"order_id": str(order.pk)}

    return execute_once(
        f"money.record:{actor.pk}",
        submission_key,
        {
            "order_id": str(order_id),
            "kind": kind,
            "amount_fen": amount_fen,
            "version": version,
            "reduction_fen": reduction_fen,
            "reason": reason,
            "occurred_at": occurred_at.isoformat() if occurred_at else None,
            "account_type": account_type,
        },
        action,
    )


def validate_cash_metadata(occurred_at, account_type):
    if account_type not in {"UNKNOWN", "BANK", "ALIPAY", "WECHAT", "CASH"}:
        raise BusinessError("请选择实际收支账户类型，托管账户不能作为卖家现金账户。")
    if occurred_at and (timezone.is_naive(occurred_at) or occurred_at > timezone.now()):
        raise BusinessError("实际收支时间须包含时区且不能在未来。")


def record_customer_payment(
    *,
    actor,
    submission_key,
    order_id,
    version,
    amount_fen,
    source_ref,
    evidence,
    occurred_at=None,
    platform_status="",
):
    require_operator(actor)
    whole(amount_fen, "客户付款金额", 1)
    validate_cash_metadata(occurred_at, "UNKNOWN")
    if (
        not source_ref.strip()
        or len(source_ref) > 200
        or not evidence.strip()
        or len(evidence) > 300
    ):
        raise BusinessError("请填写付款凭据编号（200 字以内）和核对依据（300 字以内）。")
    if len(platform_status) > 100:
        raise BusinessError("平台状态不能超过 100 字。")
    payload = dict(
        order_id=str(order_id),
        version=version,
        amount_fen=amount_fen,
        source_ref=source_ref.strip(),
        evidence=evidence,
        occurred_at=occurred_at.isoformat() if occurred_at else None,
        platform_status=platform_status,
    )

    def action():
        order = SalesOrder.objects.select_for_update().get(pk=order_id)
        existing = CustomerPaymentFact.objects.filter(
            order=order, source="MANUAL", source_ref=source_ref.strip()
        ).first()
        if existing:
            if (
                existing.amount_fen,
                existing.occurred_at,
                existing.platform_status,
                existing.evidence,
            ) != (amount_fen, occurred_at, platform_status, evidence):
                raise BusinessError("该凭据已登记且内容不同，请核对，不能覆盖原付款事实。")
            return {"order_id": str(order.pk), "payment_id": str(existing.pk)}
        if order.version != version:
            raise BusinessError("订单已变化，请刷新核对。")
        if order.status in (SalesOrder.Status.DRAFT, SalesOrder.Status.CANCELLED):
            raise BusinessError("请先确认有效订单，再登记客户付款依据。")
        if order.channel.code != "XIANYU":
            raise BusinessError("此入口记录闲鱼平台托管付款；实际收到的钱请使用收款入口。")
        fact = CustomerPaymentFact.objects.create(
            order=order,
            actor=actor,
            amount_fen=amount_fen,
            source_ref=source_ref.strip(),
            evidence=evidence,
            occurred_at=occurred_at,
            time_quality="EXACT" if occurred_at else "UNKNOWN",
            platform_status=platform_status,
        )
        order.platform_paid = True
        order.version += 1
        order.save(update_fields=["platform_paid", "version", "updated_at"])
        event(order, actor, "money.customer_payment", evidence)
        return {"order_id": str(order.pk), "payment_id": str(fact.pk)}

    return execute_once(f"money.customer_payment:{actor.pk}", submission_key, payload, action)
