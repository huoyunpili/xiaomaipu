"""Build a portable, offline HTML user manual from the maintained source."""

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "docs/manual/index.html"
page = source.read_text(encoding="utf-8")
for path in (ROOT / "docs/product/images").glob("*.png"):
    reference = f"../product/images/{path.name}"
    if reference in page:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        page = page.replace(reference, f"data:image/png;base64,{encoded}")
page = page.replace(
    "<!-- TOKENS -->",
    "<style>" + (ROOT / "app/static/tokens.css").read_text(encoding="utf-8") + "</style>",
)
target = ROOT / "docs/鱼管家使用与配置手册.html"
target.write_text(page, encoding="utf-8")
# Preserve links shared before the product rename.
(ROOT / "docs/小卖铺使用与配置手册.html").write_text(page, encoding="utf-8")
print(f"Manual built: {target.stat().st_size:,} bytes")
