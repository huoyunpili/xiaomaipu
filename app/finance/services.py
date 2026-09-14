from app.accounts.policies import require_admin, require_operator
from app.common.business import BusinessError, whole
from app.common.services import execute_once
from app.orders.models import SalesOrder
from app.orders.services import event, refresh_profit

from .models import MoneyEntry


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
):
    require_operator(actor)
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
        },
        action,
    )
