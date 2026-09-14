from datetime import timedelta

from django.contrib.auth.views import LoginView
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import LoginAttempt


class ThrottledLoginView(LoginView):
    template_name = "registration/login.html"

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
