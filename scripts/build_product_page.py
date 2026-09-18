"""Build the portable product introduction with embedded, redacted screenshots."""

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "docs/product/index.html"
page = source.read_text(encoding="utf-8")
page = page.replace("../小卖铺使用与配置手册.html", "小卖铺使用与配置手册.html")
for path in sorted((ROOT / "docs/product/images").glob("*-current.png")):
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    page = page.replace(f"images/{path.name}", f"data:image/png;base64,{encoded}")
tokens = (ROOT / "app/static/tokens.css").read_text(encoding="utf-8")
page = page.replace('<!-- PRODUCT_TOKENS -->', f'<style>{tokens}</style>')
target = ROOT / "docs/小卖铺产品介绍.html"
target.write_text(page, encoding="utf-8")
print(f"Built {target.name}: {target.stat().st_size:,} bytes")
