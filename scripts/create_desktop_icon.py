"""Render the small geometric fish mark used by the Windows launcher."""

import struct
import zlib
from pathlib import Path


def chunk(name, data):
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))


def main():
    size = 128
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            inside = (max(abs(x - 63.5) - 39, 0) ** 2 + max(abs(y - 63.5) - 39, 0) ** 2) < 24**2
            fish = ((x - 60) / 31) ** 2 + ((y - 64) / 19) ** 2 < 1
            tail = 85 <= x <= 106 and abs(y - 64) < (x - 84) * 0.85
            eye = (x - 44) ** 2 + (y - 60) ** 2 < 3.5**2
            color = (51, 112, 255, 255) if inside else (0, 0, 0, 0)
            if fish or tail:
                color = (255, 255, 255, 255)
            if eye:
                color = (51, 112, 255, 255)
            pixels.extend(color)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")
    target = Path(__file__).parents[1] / "desktop"
    (target / "icon.png").write_bytes(png)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), 22)
    (target / "icon.ico").write_bytes(header + entry + png)


if __name__ == "__main__":
    main()
