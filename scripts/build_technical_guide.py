"""Copy the self-contained public technical guide for offline sharing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / "docs/鱼管家技术说明.html"
target.write_text(
    (ROOT / "docs/technical/index.html").read_text(encoding="utf-8"),
    encoding="utf-8",
)
print(f"Built {target.name}: {target.stat().st_size:,} bytes")
