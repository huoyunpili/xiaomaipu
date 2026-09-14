from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from .business import BusinessError
from .services import IdempotencyConflict


def business_form(
    request,
    *,
    form,
    title,
    intro,
    save,
    destination,
    button="保存",
    back_url="/",
    template_name="business_form.html",
    extra_context=None,
):
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            result = save(dict(form.cleaned_data))
        except (BusinessError, IdempotencyConflict, ValidationError) as exc:
            message = "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            form.add_error(None, message)
            status = 409
        else:
            messages.success(request, "操作已保存。")
            return redirect(destination(result))
    return render(
        request,
        template_name,
        {
            "form": form,
            "title": title,
            "intro": intro,
            "button": button,
            "back_url": back_url,
            **(extra_context or {}),
        },
        status=status,
    )
