from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.accounts.policies import require_admin
from app.common.business import record_event
from app.common.form_views import business_form
from app.common.forms import SubmissionForm
from app.common.services import execute_once

from .models import SalesChannel


class ChannelForm(SubmissionForm):
    code = forms.RegexField(
        label="渠道代码（字母、数字、下划线）", regex=r"^[A-Z][A-Z0-9_]{0,31}$", max_length=32
    )
    name = forms.CharField(label="渠道名称", max_length=100)
    is_active = forms.BooleanField(label="启用此渠道", required=False, initial=True)


@login_required
@require_http_methods(["GET", "POST"])
def channel_edit(request, channel_id=None):
    require_admin(request.user)
    channel = get_object_or_404(SalesChannel, pk=channel_id) if channel_id else None
    form = ChannelForm(
        request.POST if request.method == "POST" else None,
        initial={"code": channel.code, "name": channel.name, "is_active": channel.is_active}
        if channel
        else {},
    )
    if channel:
        form.fields["code"].disabled = True

    def save(data):
        key = data.pop("submission_key")

        def action():
            record = (
                SalesChannel.objects.select_for_update().get(pk=channel_id)
                if channel
                else SalesChannel()
            )
            for field, value in data.items():
                setattr(record, field, value)
            record.full_clean()
            record.save()
            record_event(request.user, "channel.saved", record)
            return {}

        return execute_once(
            f"channel.save:{request.user.pk}",
            key,
            dict(channel_id=str(channel_id or ""), **data),
            action,
        )

    return business_form(
        request,
        form=form,
        title="销售渠道",
        intro="自定义销售来源，停用后不再用于新开单，历史订单保留。渠道代码用于表格导入，保存后不可修改。",
        save=save,
        destination=lambda result: reverse("shop-settings"),
        back_url=reverse("shop-settings"),
    )
