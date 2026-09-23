import csv
import io

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app.accounts.policies import require_operator
from app.common.forms import SubmissionForm

from .models import ImportJob
from .services import HEADERS


class ImportForm(SubmissionForm):
    file = forms.FileField(label="订单表格（CSV / XLSX）")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for index, header in enumerate(HEADERS):
            self.fields[f"column_{index}"] = forms.CharField(
                label=f"{header} 对应的表格列名", required=False, max_length=100, initial=header
            )

    @property
    def mapping_fields(self):
        return [self[f"column_{index}"] for index in range(len(HEADERS))]


@login_required
@require_http_methods(["GET", "POST"])
def import_home(request):
    messages.info(
        request,
        "本地订单文件导入已停用。请先把历史订单导入闲管家，再由鱼管家统一同步。",
    )
    return redirect("xgj-home")


@login_required
@require_GET
def import_detail(request, job_id):
    from django.core.paginator import Paginator
    from django.db.models import Count

    job = get_object_or_404(ImportJob, pk=job_id)
    return render(
        request,
        "importing/detail.html",
        {
            "job": job,
            "rows": Paginator(job.rows.all(), 50).get_page(request.GET.get("page")),
            "counts": [
                {
                    "label": {
                        "READY": "待导入",
                        "ERROR": "需修正",
                        "IMPORTED": "已导入",
                        "SKIPPED": "重复跳过",
                    }.get(row["status"], row["status"]),
                    "total": row["total"],
                }
                for row in job.rows.values("status").annotate(total=Count("id"))
            ],
        },
    )


@login_required
@require_POST
def import_confirm(request, job_id):
    require_operator(request.user)
    job = get_object_or_404(ImportJob, pk=job_id)
    messages.info(request, "本地订单文件导入已停用，历史导入记录仅供查看。")
    return redirect("import-detail", job_id=job.pk)


def csv_response(rows, name):
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow(
            [
                ("'" + str(value)) if str(value).startswith(("=", "+", "-", "@")) else value
                for value in row
            ]
        )
    response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@login_required
@require_GET
def import_template(request):
    messages.info(request, "本地订单模板已停用，请通过闲管家导入历史订单。")
    return redirect("xgj-home")


@login_required
@require_GET
def import_errors(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    return csv_response(
        [
            ["行号", "错误说明"],
            *[[row.number, row.error] for row in job.rows.filter(status="ERROR")],
        ],
        "import-errors.csv",
    )
