import socket

from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from app.desktop.middleware import DesktopSessionMiddleware
from app.desktop.runtime import reserve_web_socket


def test_desktop_web_socket_is_reserved_and_does_not_reuse_a_busy_port():
    with reserve_web_socket() as first, reserve_web_socket() as second:
        assert first.getsockname()[0] == "127.0.0.1"
        assert first.getsockname()[1] != second.getsockname()[1]
        with socket.socket() as contender:
            try:
                contender.bind(first.getsockname())
            except OSError:
                pass
            else:
                raise AssertionError("The desktop port was not reserved exclusively")


@override_settings(FISH_DESKTOP_TOKEN="synthetic-desktop-session")
def test_desktop_backend_rejects_browser_and_other_local_clients():
    middleware = DesktopSessionMiddleware(lambda request: HttpResponse("private workspace"))
    factory = RequestFactory()
    assert middleware(factory.get("/")).status_code == 404
    assert middleware(factory.get("/", HTTP_X_FISH_DESKTOP="wrong")).status_code == 404
    assert middleware(factory.get("/", HTTP_X_FISH_DESKTOP="invalid-非ASCII")).status_code == 404
    assert (
        middleware(factory.get("/", HTTP_X_FISH_DESKTOP="synthetic-desktop-session")).status_code
        == 200
    )


@override_settings(FISH_DESKTOP_TOKEN="")
def test_legacy_web_mode_still_works_without_desktop_middleware_token():
    middleware = DesktopSessionMiddleware(lambda request: HttpResponse("legacy workspace"))
    assert middleware(RequestFactory().get("/")).status_code == 200
