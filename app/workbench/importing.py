import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from app.accounts.policies import require_admin
from app.common.business import BusinessError, record_event

from .models import Trade
from .services import cost_at_payment, resolve_product
from .views import active_shop, csv_response

HEADERS = [
    "订单号",
    "商品标识",
    "商品名称",
    "型号规格",
    "数量",
    "实付金额",
    "状态",
    "下单时间",
    "付款时间",
    "发货时间",
    "完成时间",
    "退款时间",
    "收件人",
    "电话",
    "完整地址",
    "供应商",
]
OPTIONAL_HEADERS = ["规格标识"]
TIME_FIELDS = [
    ("ordered_at", "下单时间"),
    ("paid_at", "付款时间"),
    ("shipped_at", "发货时间"),
    ("completed_at", "完成时间"),
    ("refunded_at", "退款时间"),
]


def parse_time(value):
    if not value:
        return None
    result = datetime.fromisoformat(value.strip())
    return result if result.tzinfo else result.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


@transaction.atomic
def import_rows(content, actor):
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or any(k not in reader.fieldnames for k in HEADERS):
        raise BusinessError("表头不符，请使用本页 CSV 模板。")
    states = {label: code for code, label in Trade.Status.choices}
    count, skipped = 0, 0
    for index, row in enumerate(reader, 2):
        if index > 10001:
            raise BusinessError("每次最多导入一万笔订单。")
        try:
            number = row["订单号"].strip()
            if not number.isdigit() or len(number) > 100:
                raise ValueError()
            state = states[row["状态"]]
            qty = int(row["数量"])
            amount = Decimal(row["实付金额"]) * 100
            if (
                not amount.is_finite()
                or amount != amount.to_integral_value()
                or not 0 <= amount <= 10**12
                or not 1 <= qty <= 1000000
            ):
                raise ValueError()
            times = {field: parse_time(row[key]) for field, key in TIME_FIELDS}
            if not times["ordered_at"] or (
                state != "UNPAID" and state != "CLOSED" and not times["paid_at"]
            ):
                raise ValueError()
            if state == "COMPLETED" and not times["completed_at"]:
                raise ValueError()
            if state == "REFUNDED" and not times["refunded_at"]:
                raise ValueError()
            if state == "PENDING" and not times["shipped_at"]:
                raise ValueError()
            limits = {
                "商品标识": 200,
                "商品名称": 200,
                "型号规格": 200,
                "收件人": 100,
                "电话": 100,
                "完整地址": 1000,
                "供应商": 100,
            }
            if any(len(row.get(key) or "") > limit for key, limit in limits.items()):
                raise ValueError()
            if not row["商品标识"].strip():
                raise ValueError()
        except (ValueError, KeyError, InvalidOperation, AttributeError) as exc:
            raise BusinessError(
                f"第 {index} 行格式错误，请检查订单号、商品、数量、金额、状态及关键时间。整批尚未写入。"
            ) from exc
        if Trade.objects.filter(shop=active_shop(), number=number).exists():
            skipped += 1
            continue
        goods = {
            "product_id": row["商品标识"].strip(),
            "sku_text": row["型号规格"].strip(),
            "sku_id": (row.get("规格标识") or "").strip(),
            "title": row["商品名称"][:200],
        }
        product = resolve_product(goods, active_shop())
        unit_cost, cost_version = cost_at_payment(product, times["paid_at"])
        trade = Trade.objects.create(
            shop=active_shop(),
            number=number,
            source="IMPORT",
            status=state,
            status_changed_at=times["refunded_at"]
            or times["completed_at"]
            or times["shipped_at"]
            or times["paid_at"]
            or times["ordered_at"],
            product=product,
            title=row["商品名称"][:200],
            spec=row["型号规格"][:200],
            quantity=qty,
            paid_fen=int(amount),
            unit_cost_fen=unit_cost,
            cost_version=cost_version,
            receiver=row["收件人"][:100],
            phone=row["电话"][:100],
            address=row["完整地址"][:1000],
            supplier=row["供应商"][:100] or product.supplier,
            supplier_override=bool(row["供应商"]),
            supplier_wechat=product.supplier_wechat if not row["供应商"] else "",
            default_shipping_note=product.shipping_note,
            **times,
        )
        record_event(actor, "workbench.imported", trade)
        count += 1
    return count, skipped


@login_required
@require_http_methods(["GET", "POST"])
def history(request):
    require_admin(request.user)
    if request.GET.get("template"):
        return csv_response(HEADERS + OPTIONAL_HEADERS, [], "history-template.csv")
    if request.method == "POST":
        upload = request.FILES.get("file")
        try:
            if not upload or upload.size > 10 * 1024 * 1024:
                raise BusinessError("请选择不超过 10MB 的 UTF-8 CSV 文件。")
            count, skipped = import_rows(upload.read().decode("utf-8-sig"), request.user)
            messages.success(request, f"成功导入 {count} 单，跳过 {skipped} 条重复订单。")
            return redirect("wb-orders")
        except (BusinessError, UnicodeError, csv.Error) as exc:
            messages.error(
                request, str(exc) if isinstance(exc, BusinessError) else "文件编码或 CSV 格式错误。"
            )
    return render(request, "workbench/history.html")
