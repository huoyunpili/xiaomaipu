from datetime import timedelta
from unittest.mock import patch

from django.template.loader import render_to_string
from django.utils import timezone

from app.integrations.models import Connection


def test_stopped_sync_warns_even_when_previous_run_has_no_error():
    now = timezone.now()
    connection = Connection(enabled=True, last_success=now - timedelta(minutes=11), error="")
    with patch("app.integrations.models.timezone.now", return_value=now):
        assert "超过 10 分钟" in connection.sync_warning
        html = render_to_string("workbench/sync.html", {"connection": connection})
        assert 'role="alert"' in html
        assert "订单数量可能落后于平台" in html
        connection.last_success = now - timedelta(minutes=5)
        assert connection.sync_warning == ""
        html = render_to_string("workbench/sync.html", {"connection": connection})
        assert 'role="alert"' not in html


def test_disabled_and_never_completed_sync_explain_missing_data():
    connection = Connection(enabled=False)
    assert "自动同步已暂停" in connection.sync_warning
    connection.enabled = True
    assert "尚未成功同步" in connection.sync_warning
