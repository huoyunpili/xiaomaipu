from unittest.mock import patch

from app.common.templatetags.assets import versioned_static


def test_asset_url_changes_with_content_not_with_page_reload(tmp_path):
    asset = tmp_path / "component.css"
    asset.write_text("old", encoding="utf-8")
    with patch("app.common.templatetags.assets.finders.find", return_value=str(asset)):
        original = versioned_static("component.css")
        assert "?v=" in original
        assert versioned_static("component.css") == original
        asset.write_text("updated styles", encoding="utf-8")
        assert versioned_static("component.css") != original
