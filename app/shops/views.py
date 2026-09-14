import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from app.accounts.policies import require_admin
from app.common.services import IdempotencyConflict

from .forms import ShopSettingsForm
from .models import SalesChannel, Shop
from .services import StaleVersion, update_shop


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    require_admin(request.user)
    shop = Shop.objects.filter(is_active=True).first()
    if shop is None:
        return render(request, "setup_required.html", status=503)
    form = ShopSettingsForm(
        request.POST if request.method == "POST" else None,
        initial={"name": shop.name, "version": shop.version, "submission_key": uuid.uuid4()},
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            update_shop(actor=request.user, request_id=request.request_id, **form.cleaned_data)
        except (StaleVersion, IdempotencyConflict) as exc:
            form.add_error(None, str(exc))
            status = 409
        else:
            messages.success(request, "店铺资料已保存。")
            return redirect("shop-settings")
    return render(
        request,
        "shops/settings.html",
        {"form": form, "channels": SalesChannel.objects.all()},
        status=status,
    )
