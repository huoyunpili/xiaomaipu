import hmac

from django.conf import settings
from django.http import HttpResponseNotFound


class DesktopSessionMiddleware:
    """Only the desktop window may reach its private loopback backend."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        expected = getattr(settings, "FISH_DESKTOP_TOKEN", "")
        supplied = request.headers.get("X-Fish-Desktop", "")
        if expected and not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return HttpResponseNotFound()
        return self.get_response(request)
