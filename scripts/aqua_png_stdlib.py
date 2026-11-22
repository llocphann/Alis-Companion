#!/usr/bin/env python3
"""Zero-dependency pure-stdlib PNG/JPEG helper replacing Pillow in Alis-Companion tests and tooling."""
from __future__ import annotations

import io
import os
from pathlib import Path
import struct
import zlib


class DecompressionBombError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    n = len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
            continue
        if i + 2 > n:
            break
        seg_len = struct.unpack(">H", data[i : i + 2])[0]
        if seg_len < 2 or i + seg_len > n:
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if seg_len >= 7:
                h, w = struct.unpack(">HH", data[i + 3 : i + 7])
                return int(w), int(h)
            break
        i += seg_len
    return None


class _ChannelView:
    def __init__(self, values: bytes):
        self._values = values

    def getextrema(self) -> tuple[int, int]:
        if not self._values:
            return (0, 0)
        return (min(self._values), max(self._values))


class StdlibImage:
    def __init__(self, mode: str, size: tuple[int, int], data: bytearray, fmt: str = "PNG"):
        self.mode = mode
        self.width = int(size[0])
        self.height = int(size[1])
        self.size = (self.width, self.height)
        self._data = data
        self.format = fmt

    def __enter__(self) -> "StdlibImage":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def close(self) -> None:
        pass

    @property
    def _bpp(self) -> int:
        return {"RGBA": 4, "RGB": 3, "L": 1}.get(self.mode, 4)

    def tobytes(self) -> bytes:
        return bytes(self._data)

    def copy(self) -> "StdlibImage":
        return StdlibImage(self.mode, self.size, bytearray(self._data), fmt=self.format)

    def convert(self, target_mode: str) -> "StdlibImage":
        if target_mode == self.mode:
            return self.copy()
        w, h = self.width, self.height
        src = self._data
        if target_mode == "RGBA":
            out = bytearray(w * h * 4)
            if self.mode == "RGB":
                for i in range(w * h):
                    si = i * 3
                    di = i * 4
                    out[di] = src[si]
                    out[di + 1] = src[si + 1]
                    out[di + 2] = src[si + 2]
                    out[di + 3] = 255
            elif self.mode == "L":
                for i in range(w * h):
                    v = src[i]
                    di = i * 4
                    out[di] = v
                    out[di + 1] = v
                    out[di + 2] = v
                    out[di + 3] = 255
            return StdlibImage("RGBA", (w, h), out, fmt=self.format)
        if target_mode == "RGB":
            out = bytearray(w * h * 3)
            if self.mode == "RGBA":
                for i in range(w * h):
                    si = i * 4
                    di = i * 3
                    out[di] = src[si]
                    out[di + 1] = src[si + 1]
                    out[di + 2] = src[si + 2]
            elif self.mode == "L":
                for i in range(w * h):
                    v = src[i]
                    di = i * 3
                    out[di] = v
                    out[di + 1] = v
                    out[di + 2] = v
            return StdlibImage("RGB", (w, h), out, fmt=self.format)
        if target_mode == "L":
            out = bytearray(w * h)
            bpp = self._bpp
            for i in range(w * h):
                si = i * bpp
                out[i] = (299 * src[si] + 587 * src[si + 1] + 114 * src[si + 2]) // 1000
            return StdlibImage("L", (w, h), out, fmt=self.format)
        raise ValueError(f"Unsupported mode conversion: {self.mode} -> {target_mode}")

    def getpixel(self, xy: tuple[int, int]):
        x, y = int(xy[0]), int(xy[1])
        bpp = self._bpp
        off = (y * self.width + x) * bpp
        if bpp == 1:
            return self._data[off]
        return tuple(self._data[off : off + bpp])

    def putpixel(self, xy: tuple[int, int], val) -> None:
        x, y = int(xy[0]), int(xy[1])
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        bpp = self._bpp
        off = (y * self.width + x) * bpp
        if bpp == 1:
            self._data[off] = int(val) & 0xFF
        else:
            for k in range(bpp):
                self._data[off + k] = int(val[k]) & 0xFF

    def getchannel(self, channel: str) -> _ChannelView:
        idx_map = {"R": 0, "G": 1, "B": 2, "A": 3}
        if channel not in idx_map:
            raise ValueError(f"Unknown channel: {channel}")
        idx = idx_map[channel]
        bpp = self._bpp
        if idx >= bpp:
            return _ChannelView(bytes([255] * (self.width * self.height)))
        return _ChannelView(bytes(self._data[idx::bpp]))

    def save(self, fp, format: str | None = None, **_kwargs) -> None:
        w, h = self.width, self.height
        if self.mode == "RGBA":
            ctype, bpp = 6, 4
        elif self.mode == "RGB":
            ctype, bpp = 2, 3
        elif self.mode == "L":
            ctype, bpp = 0, 1
        else:
            self.convert("RGBA").save(fp, format=format)
            return
        stride = w * bpp
        raw_rows = bytearray()
        for y in range(h):
            raw_rows.append(0)
            raw_rows.extend(self._data[y * stride : (y + 1) * stride])
        ihdr = struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)
        idat = zlib.compress(bytes(raw_rows), level=1)
        png_bytes = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
        if hasattr(fp, "write"):
            fp.write(png_bytes)
        else:
            Path(fp).write_bytes(png_bytes)


class _Draw:
    def __init__(self, image: StdlibImage):
        self.image = image

    def rectangle(self, xy, fill=None, outline=None) -> None:
        if fill is None:
            return
        if len(xy) == 2 and isinstance(xy[0], (tuple, list)):
            x0, y0 = int(xy[0][0]), int(xy[0][1])
            x1, y1 = int(xy[1][0]), int(xy[1][1])
        else:
            x0, y0, x1, y1 = int(xy[0]), int(xy[1]), int(xy[2]), int(xy[3])
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        w, h = self.image.width, self.image.height
        x0 = max(0, min(w - 1, x0))
        x1 = max(0, min(w - 1, x1))
        y0 = max(0, min(h - 1, y0))
        y1 = max(0, min(h - 1, y1))
        bpp = self.image._bpp
        if isinstance(fill, int):
            px = bytes([fill & 0xFF] * bpp)
        else:
            vals = list(fill)
            while len(vals) < bpp:
                vals.append(255)
            px = bytes(int(v) & 0xFF for v in vals[:bpp])
        span = px * (x1 - x0 + 1)
        data = self.image._data
        for y in range(y0, y1 + 1):
            start = (y * w + x0) * bpp
            data[start : start + len(span)] = span

    def ellipse(self, xy, fill=None, outline=None) -> None:
        if fill is None:
            return
        if len(xy) == 2 and isinstance(xy[0], (tuple, list)):
            x0, y0 = int(xy[0][0]), int(xy[0][1])
            x1, y1 = int(xy[1][0]), int(xy[1][1])
        else:
            x0, y0, x1, y1 = int(xy[0]), int(xy[1]), int(xy[2]), int(xy[3])
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        rx = max(0.5, (x1 - x0) / 2.0)
        ry = max(0.5, (y1 - y0) / 2.0)
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        w, h = self.image.width, self.image.height
        bpp = self.image._bpp
        if isinstance(fill, int):
            px = bytes([fill & 0xFF] * bpp)
        else:
            vals = list(fill)
            while len(vals) < bpp:
                vals.append(255)
            px = bytes(int(v) & 0xFF for v in vals[:bpp])
        data = self.image._data
        for y in range(max(0, y0), min(h - 1, y1) + 1):
            dy = (y - cy) / ry
            rem = 1.0 - dy * dy
            if rem < 0.0:
                continue
            dx_max = int(rx * (rem ** 0.5))
            sx0 = max(0, int(cx - dx_max))
            sx1 = min(w - 1, int(cx + dx_max))
            if sx1 >= sx0:
                span = px * (sx1 - sx0 + 1)
                start = (y * w + sx0) * bpp
                data[start : start + len(span)] = span


class ImageDraw:
    @staticmethod
    def Draw(image: StdlibImage) -> _Draw:
        return _Draw(image)


def _decode_png_bytes(data: bytes) -> StdlibImage:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not_a_png")
    pos = 8
    width = height = bit_depth = color_type = interlace = 0
    idat_parts: list[bytes] = []
    palette = b""
    trns = b""
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        pos += 8
        chunk = data[pos : pos + length]
        pos += length + 4
        if ctype == b"IHDR" and len(chunk) >= 13:
            width, height, bit_depth, color_type, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk[:13])
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"tRNS":
            trns = chunk
        elif ctype == b"IDAT":
            idat_parts.append(chunk)
        elif ctype == b"IEND":
            break
    if width <= 0 or height <= 0 or bit_depth != 8 or interlace != 0:
        raise ValueError("unsupported_png")
    if width * height > 16_000_000:
        raise DecompressionBombError("png_too_large")
    channels_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_map.get(color_type)
    if not channels:
        raise ValueError("unsupported_png_color_type")
    raw = zlib.decompress(b"".join(idat_parts))
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise ValueError("truncated_png")
    prev = bytearray(stride)
    rows = bytearray()
    offset = 0
    for _y in range(height):
        flt = raw[offset]
        offset += 1
        scan = bytearray(raw[offset : offset + stride])
        offset += stride
        if flt == 1:
            for i in range(channels, stride):
                scan[i] = (scan[i] + scan[i - channels]) & 0xFF
        elif flt == 2:
            for i in range(stride):
                scan[i] = (scan[i] + prev[i]) & 0xFF
        elif flt == 3:
            for i in range(stride):
                left = scan[i - channels] if i >= channels else 0
                scan[i] = (scan[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif flt == 4:
            for i in range(stride):
                left = scan[i - channels] if i >= channels else 0
                up = prev[i]
                up_left = prev[i - channels] if i >= channels else 0
                scan[i] = (scan[i] + _paeth(left, up, up_left)) & 0xFF
        elif flt != 0:
            raise ValueError(f"unsupported_png_filter:{flt}")
        prev = scan
        rows.extend(scan)

    if color_type == 6:
        return StdlibImage("RGBA", (width, height), rows, fmt="PNG")
    if color_type == 2:
        return StdlibImage("RGB", (width, height), rows, fmt="PNG")
    if color_type == 0:
        return StdlibImage("L", (width, height), rows, fmt="PNG")
    if color_type == 4:
        out = bytearray(width * height * 4)
        for i in range(width * height):
            g, a = rows[i * 2], rows[i * 2 + 1]
            di = i * 4
            out[di] = out[di + 1] = out[di + 2] = g
            out[di + 3] = a
        return StdlibImage("RGBA", (width, height), out, fmt="PNG")
    if color_type == 3:
        out = bytearray(width * height * 4)
        for i in range(width * height):
            pidx = rows[i]
            di = i * 4
            if pidx * 3 + 2 < len(palette):
                out[di] = palette[pidx * 3]
                out[di + 1] = palette[pidx * 3 + 1]
                out[di + 2] = palette[pidx * 3 + 2]
            out[di + 3] = trns[pidx] if pidx < len(trns) else 255
        return StdlibImage("RGBA", (width, height), out, fmt="PNG")
    raise ValueError("unsupported_png")


class Image:
    DecompressionBombError = DecompressionBombError

    @staticmethod
    def open(fp) -> StdlibImage:
        if isinstance(fp, (str, os.PathLike)):
            data = Path(fp).read_bytes()
        elif hasattr(fp, "read"):
            data = fp.read()
        else:
            raise ValueError("unsupported_image_input")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return _decode_png_bytes(data)
        jdim = _jpeg_dimensions(data)
        if jdim is not None:
            w, h = jdim
            return StdlibImage("RGB", (w, h), bytearray(), fmt="JPEG")
        raise ValueError("unsupported_image_format")

    @staticmethod
    def new(mode: str, size: tuple[int, int], color=0) -> StdlibImage:
        w, h = int(size[0]), int(size[1])
        bpp = {"RGBA": 4, "RGB": 3, "L": 1}.get(mode)
        if not bpp:
            raise ValueError(f"Unsupported mode: {mode}")
        if isinstance(color, int):
            px = bytes([color & 0xFF] * bpp)
        else:
            vals = list(color)
            while len(vals) < bpp:
                vals.append(255)
            px = bytes(int(v) & 0xFF for v in vals[:bpp])
        return StdlibImage(mode, (w, h), bytearray(px * (w * h)), fmt="PNG")

    @staticmethod
    def frombytes(mode: str, size: tuple[int, int], data: bytes) -> StdlibImage:
        return StdlibImage(mode, size, bytearray(data), fmt="PNG")
