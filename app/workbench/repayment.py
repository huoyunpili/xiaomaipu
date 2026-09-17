from datetime import datetime, time, timedelta

from django.utils import timezone

PERIODS = {
    "today": "今日剩余预计回款",
    "soon": "未来三天预计回款（从明天起）",
    "overdue": "预计回款时间已过，订单仍未完成",
    "unknown": "缺少发货时间，回款日期待核对",
}


def due_bounds(period, now):
    today = timezone.localdate(now)
    midnight = timezone.make_aware(datetime.combine(today, time.min))
    if period == "today":
        return midnight, midnight + timedelta(days=1)
    if period == "soon":
        return midnight + timedelta(days=1), midnight + timedelta(days=4)
    return None, now


def due_rows(rows, period, now):
    if period == "unknown":
        return [
            row for row in rows if row.status == "PENDING" and row.paid_at and not row.shipped_at
        ]
    start, end = due_bounds(period, now)
    return [
        row
        for row in rows
        if row.reference_at
        and row.paid_at
        and (start is None or row.reference_at >= start)
        and row.reference_at < end
    ]


def filter_due(qs, period, days, now):
    if period == "unknown":
        return qs.filter(status="PENDING", paid_at__isnull=False, shipped_at__isnull=True)
    start, end = due_bounds(period, now)
    shift = timedelta(days=days)
    qs = qs.filter(status="PENDING", paid_at__isnull=False, shipped_at__lt=end - shift)
    return qs.filter(shipped_at__gte=start - shift) if start else qs
