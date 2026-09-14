from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.management import call_command
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_http_methods

from app.audit.models import AuditEvent

from .forms import AccountSettingsForm, FirstOwnerForm
from .models import LoginAttempt, User

SETUP_LOCK_KEY = "first-owner-setup"


def _active_administrators():
    return User.objects.filter(is_active=True).filter(
        Q(role=User.Role.ADMIN) | Q(is_superuser=True)
    )


def _claimable_development_owner():
    administrators = _active_administrators()
    if administrators.count() != 1:
        return None
    owner = administrators.first()
    if owner and owner.username == "dev-owner" and owner.last_login is None:
        return owner
    return None


def first_owner_setup_available():
    return not _active_administrators().exists() or _claimable_development_owner() is not None


class ThrottledLoginView(LoginView):
    template_name = "registration/login.html"

    def dispatch(self, request, *args, **kwargs):
        if first_owner_setup_available():
            return redirect("first-owner-setup")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        now = timezone.now()
        username = request.POST.get("username", "").strip().casefold()[:150]
        address = request.META.get("REMOTE_ADDR", "")
        keys = [
            (salted_hmac("login-account", username, algorithm="sha256").hexdigest(), 10),
            (salted_hmac("login-ip", address, algorithm="sha256").hexdigest(), 60),
        ]
        blocked = False
        with transaction.atomic():
            for key, limit in sorted(keys):
                attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(
                    key=key, defaults={"expires_at": now + timedelta(minutes=15)}
                )
                if attempt.expires_at <= now:
                    attempt.count = 0
                    attempt.expires_at = now + timedelta(minutes=15)
                if attempt.count >= limit:
                    blocked = True
                else:
                    attempt.count += 1
                    attempt.save()
        if blocked:
            response = render(
                request,
                "error.html",
                {"message": "登录尝试过于频繁，请 15 分钟后再试。"},
                status=429,
            )
            response["Retry-After"] = "900"
            return response
        response = super().post(request, *args, **kwargs)
        if response.status_code == 302:
            LoginAttempt.objects.filter(pk=keys[0][0]).delete()
        LoginAttempt.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        return response


@require_http_methods(["GET", "POST"])
def first_owner_setup(request):
    if not first_owner_setup_available():
        return redirect("login")
    form = FirstOwnerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            LoginAttempt.objects.select_for_update().get_or_create(
                key=SETUP_LOCK_KEY,
                defaults={"expires_at": timezone.now() + timedelta(days=1)},
            )
            existing = _claimable_development_owner()
            if _active_administrators().exists() and existing is None:
                messages.error(request, "店主账号已经创建，请直接登录。")
                return redirect("login")
            if existing is None:
                owner = form.save(commit=False)
            else:
                owner = existing
                owner.username = form.cleaned_data["username"]
                owner.display_name = form.cleaned_data["display_name"]
                owner.set_password(form.cleaned_data["password1"])
            owner.role = User.Role.ADMIN
            owner.is_staff = True
            owner.is_superuser = True
            owner.is_active = True
            owner.save()
        call_command("bootstrap", verbosity=0)
        login(request, owner, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "店主账号已经创建，现在可以开始经营。")
        return redirect("dashboard")
    return render(request, "registration/first_owner.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def account_settings(request):
    original_username = request.user.username
    form = AccountSettingsForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        password_changed = bool(form.cleaned_data.get("password1"))
        user = form.save()
        if password_changed:
            update_session_auth_hash(request, user)
        AuditEvent.objects.create(
            actor=user,
            action="account.updated",
            object_id=str(user.pk),
            request_id=getattr(request, "request_id", ""),
            details={
                "username_changed": original_username != user.username,
                "password_changed": password_changed,
            },
        )
        messages.success(request, "账号资料已保存。")
        return redirect("account-settings")
    return render(request, "registration/account_settings.html", {"form": form})
