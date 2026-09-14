from django.db import models

from app.common.models import BaseModel


class ImportJob(BaseModel):
    @property
    def status_label(self):
        return {
            "PREVIEW": "待确认",
            "QUEUED": "排队中",
            "RUNNING": "处理中",
            "DONE": "已完成",
            "FAILED": "处理失败",
        }.get(self.status, self.status)

    file_hash = models.CharField(max_length=64, unique=True)
    filename = models.CharField(max_length=200)
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default="PREVIEW")
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ImportRow(BaseModel):
    @property
    def status_label(self):
        return {
            "READY": "待导入",
            "ERROR": "需修正",
            "IMPORTED": "已导入",
            "SKIPPED": "已有订单，已跳过",
        }.get(self.status, self.status)

    job = models.ForeignKey(ImportJob, on_delete=models.PROTECT, related_name="rows")
    number = models.PositiveIntegerField()
    payload = models.JSONField(default=dict)
    error = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, default="READY")
    order = models.ForeignKey("orders.SalesOrder", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["job", "number"], name="unique_import_row")]
