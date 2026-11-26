#!/usr/bin/env python3
"""Generate Wordroom's PNG app icons with only the Python standard library."""
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND = (25, 63, 53)
FOREGROUND = (222, 240, 188)
ACCENT = (200, 218, 179)
POLYGON = [(14, 18), (25, 18), (32, 45), (39, 18), (50, 18), (39, 48), (25, 48)]


def inside_polygon(x, y):
    inside = False
    previous = POLYGON[-1]
    for current in POLYGON:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def pixel(x, y):
    if inside_polygon(x, y):
        return FOREGROUND
    if 29 <= x <= 35 and 16 <= y <= 30:
        return ACCENT
    return BACKGROUND


def write_icon(path, size):
    samples = (0.125, 0.375, 0.625, 0.875)
    rows = []
    for row in range(size):
        line = bytearray([0])
        for column in range(size):
            totals = [0, 0, 0]
            for dy in samples:
                for dx in samples:
                    color = pixel((column + dx) / size * 64, (row + dy) / size * 64)
                    for channel in range(3):
                        totals[channel] += color[channel]
            line.extend(round(total / 16) for total in totals)
        rows.append(bytes(line))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main():
    target = ROOT / "public" / "icons"
    target.mkdir(parents=True, exist_ok=True)
    for filename, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        write_icon(target / filename, size)
        print(f"Generated {target / filename}")


if __name__ == "__main__":
    main()
