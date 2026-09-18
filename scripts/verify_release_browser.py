"""Smoke-test the isolated release installation; keep synthetic credentials outside artifacts."""

import json
import secrets
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local-release/verification-v131"
OUTPUT = ROOT / "artifacts/browser/release-v131"
BASE = "http://127.0.0.1:18766"


def main():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    credentials_path = PRIVATE / "synthetic-browser-credentials.json"
    if credentials_path.exists():
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    else:
        credentials = {"username": "synthetic-release-owner", "password": secrets.token_urlsafe(30)}
        credentials_path.write_text(json.dumps(credentials), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE)
        if "/setup/" in page.url:
            page.get_by_label("用户名", exact=False).fill(credentials["username"])
            page.get_by_label("设置密码").fill(credentials["password"])
            page.get_by_label("再输入一次密码").fill(credentials["password"])
            page.get_by_role("button", name="创建账号并进入后台").click()
        else:
            page.get_by_label("用户名").fill(credentials["username"])
            page.get_by_label("密码").fill(credentials["password"])
            page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_role("heading", name="鱼管家")).to_be_visible()
        page.goto(BASE + "/setup/")
        assert "/setup/" not in page.url
        for width, height in [(1365, 900), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            for name, route in [
                ("dashboard", "/"),
                ("shipping", "/workspace/shipping/"),
                ("refunds", "/workspace/refunds/"),
                ("profits", "/workspace/profits/"),
                ("settings", "/workspace/settings/"),
            ]:
                page.goto(BASE + route)
                expect(page.locator("h1")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(OUTPUT / f"{name}-{width}.png"), full_page=True)
        assert not errors
        context.storage_state(path=str(PRIVATE / "synthetic-browser-session.json"))
        browser.close()
    (OUTPUT / "report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "first_owner_or_login": True,
                "setup_closed": True,
                "desktop_mobile_pages": 10,
                "javascript_errors": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Isolated release browser checks passed.")


if __name__ == "__main__":
    main()
