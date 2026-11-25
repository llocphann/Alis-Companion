#!/usr/bin/env python3
"""Pure-stdlib bridge from Alis-Companion to Alis's native Rust toolchain.

Routes desktop capture, image resizing, 64-bit dHash, backlight control,
Wayland clipboard, virtual keyboard input, and MPRIS2 D-Bus media control
directly to Alis's native Rust binaries (`alis-native`, `alis-inputd`,
`alis-theme`, or `scripts/native-dispatch`) with zero Pillow dependency.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zlib

_COMPANION_ROOT = Path(__file__).resolve().parents[2]


def is_test_or_isolated_env() -> bool:
    return bool(
        os.environ.get("ALIS_AQUA_TEST_NO_SPAWN") == "1"
        or os.environ.get("ALIS_TEST_ISOLATED") == "1"
        or os.environ.get("AQUA_TEST_ENDPOINT")
        or os.environ.get("PYTEST_CURRENT_TEST")
    )


def _candidate_host_roots(host_root: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | str | None) -> None:
        if not p:
            return
        try:
            resolved = Path(p).expanduser().resolve(strict=False)
        except OSError:
            return
        key = str(resolved)
        if key not in seen and resolved.is_dir():
            seen.add(key)
            roots.append(resolved)

    _add(host_root)
    _add(os.environ.get("ALIS_ROOT", "").strip())
    _add(os.environ.get("ALIS_SHELL_ROOT", "").strip())
    _add(_COMPANION_ROOT.parent / "Alis")
    # Installed Companion package lives at ~/.local/share/alis-companion/releases/<sha>;
    # live Alis host lives at ~/.config/quickshell/alis.
    _add(Path.home() / ".config/quickshell/alis")
    return roots


def find_native_binary(binary_name: str, host_root: Path | None = None) -> str | None:
    """Locate an executable Alis Rust binary (`alis-native`, `alis-inputd`, `alis-theme`, `alis-mpdd`)."""
    override_dir = os.environ.get("ALIS_NATIVE_BIN_DIR", "").strip()
    if override_dir:
        cand = Path(override_dir) / binary_name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)

    state_bin_file = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "alis/native-bin-dir"
    if state_bin_file.is_file():
        try:
            saved_dir = state_bin_file.read_text(encoding="utf-8").strip()
            if saved_dir and "/.debris/" not in saved_dir:
                cand = Path(saved_dir) / binary_name
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
        except OSError:
            pass

    for root in _candidate_host_roots(host_root):
        for rel in ("native/bin", "native/target/release"):
            cand = root / rel / binary_name
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)

    found = shutil.which(binary_name)
    if found:
        return found
    return None


def find_native_dispatch(host_root: Path | None = None) -> str | None:
    for root in _candidate_host_roots(host_root):
        cand = root / "scripts/native-dispatch"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _run_native(
    subcmd: str,
    dispatch_args: list[str],
    *,
    direct_binary: str = "",
    direct_args: list[str] | None = None,
    input_bytes: bytes | None = None,
    timeout: float = 4.0,
    host_root: Path | None = None,
    allow_in_isolated: bool = False,
) -> subprocess.CompletedProcess | None:
    if not allow_in_isolated and is_test_or_isolated_env():
        return None
    cmd: list[str] | None = None
    if direct_binary:
        bpath = find_native_binary(direct_binary, host_root=host_root)
        if bpath:
            cmd = [bpath, *(direct_args if direct_args is not None else dispatch_args)]
    if cmd is None:
        dispatch = find_native_dispatch(host_root=host_root)
        if dispatch:
            cmd = [dispatch, subcmd, *dispatch_args]
    if cmd is None:
        return None
    try:
        return subprocess.run(
            cmd,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def has_wayland_session() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY", "").strip())


# ---------------------------------------------------------------------------
# Pure-stdlib image decoding & 64-bit dHash (zero Pillow dependency)
# ---------------------------------------------------------------------------

def _dhash_from_gray_9x8(gray_pixels: list[int]) -> str:
    if len(gray_pixels) < 72:
        return ""
    bits = 0
    for row in range(8):
        row_start = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if gray_pixels[row_start + col] > gray_pixels[row_start + col + 1] else 0)
    return f"{bits:016x}"


def _downsample_gray_to_9x8(width: int, height: int, luma_rows: list[bytes] | list[list[int]]) -> list[int]:
    if width <= 0 or height <= 0 or not luma_rows:
        return []
    out: list[int] = []
    for ty in range(8):
        y0 = (ty * height) // 8
        y1 = max(y0 + 1, ((ty + 1) * height) // 8)
        for tx in range(9):
            x0 = (tx * width) // 9
            x1 = max(x0 + 1, ((tx + 1) * width) // 9)
            acc = 0
            cnt = 0
            for y in range(y0, min(y1, height)):
                row = luma_rows[y]
                for x in range(x0, min(x1, width)):
                    acc += row[x]
                    cnt += 1
            out.append(acc // cnt if cnt else 0)
    return out


def compute_dhash_from_ppm_bytes(data: bytes) -> str:
    """Compute a 64-bit dHash directly from Netpbm P2/P3/P5/P6 or PAM P7 bytes (pure stdlib)."""
    if not data or len(data) < 9:
        return ""
    if data.startswith(b"P7"):
        end_idx = data.find(b"ENDHDR")
        if end_idx == -1:
            return ""
        nl_after = data.find(b"\n", end_idx)
        if nl_after == -1:
            return ""
        header_lines = data[:end_idx].decode("ascii", errors="replace").splitlines()
        width = height = depth = maxval = 0
        for ln in header_lines[1:]:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split(None, 1)
            if len(parts) != 2:
                continue
            key, val = parts[0].upper(), parts[1].strip()
            try:
                if key == "WIDTH":
                    width = int(val)
                elif key == "HEIGHT":
                    height = int(val)
                elif key == "DEPTH":
                    depth = int(val)
                elif key == "MAXVAL":
                    maxval = int(val)
            except ValueError:
                return ""
        if width <= 0 or height <= 0 or depth not in (1, 2, 3, 4) or maxval <= 0 or width * height > 20_000_000:
            return ""
        sample_bytes = 1 if maxval < 256 else 2
        bpp = depth * sample_bytes
        raw = data[nl_after + 1 :]
        if len(raw) < width * height * bpp:
            return ""
        scale = 255.0 / maxval if maxval != 255 else 1.0
        luma_rows: list[list[int]] = []
        pos = 0
        for _y in range(height):
            row: list[int] = []
            for _x in range(width):
                if depth in (1, 2):
                    g = raw[pos] if sample_bytes == 1 else ((raw[pos] << 8) | raw[pos + 1])
                    lum = int(g * scale)
                else:
                    if sample_bytes == 1:
                        r, g, b_ch = raw[pos], raw[pos + 1], raw[pos + 2]
                    else:
                        r = (raw[pos] << 8) | raw[pos + 1]
                        g = (raw[pos + 2] << 8) | raw[pos + 3]
                        b_ch = (raw[pos + 4] << 8) | raw[pos + 5]
                    lum = int((299 * r + 587 * g + 114 * b_ch) * scale) // 1000
                pos += bpp
                row.append(max(0, min(255, lum)))
            luma_rows.append(row)
        if width == 9 and height == 8:
            return _dhash_from_gray_9x8([px for row in luma_rows for px in row])
        return _dhash_from_gray_9x8(_downsample_gray_to_9x8(width, height, luma_rows))

    idx = 0
    n = len(data)
    tokens: list[bytes] = []
    while len(tokens) < 4 and idx < n:
        b = data[idx]
        if b in (9, 10, 13, 32):
            idx += 1
            continue
        if b == 35:  # '#' comment
            nl = data.find(b"\n", idx)
            idx = n if nl == -1 else nl + 1
            continue
        start = idx
        while idx < n and data[idx] not in (9, 10, 13, 32, 35):
            idx += 1
        tokens.append(data[start:idx])
    if len(tokens) < 4:
        return ""
    magic = tokens[0]
    try:
        width = int(tokens[1])
        height = int(tokens[2])
        maxval = int(tokens[3])
    except ValueError:
        return ""
    if width <= 0 or height <= 0 or maxval <= 0 or width * height > 20_000_000:
        return ""
    # Skip single whitespace byte after maxval in binary formats
    if idx < n and data[idx] in (9, 10, 13, 32):
        idx += 1

    scale = 255.0 / maxval if maxval != 255 else 1.0
    if magic == b"P6":
        bpp = 3 if maxval < 256 else 6
        raw = data[idx:]
        if len(raw) < width * height * bpp:
            return ""
        luma_rows: list[list[int]] = []
        pos = 0
        for _y in range(height):
            row: list[int] = []
            for _x in range(width):
                if bpp == 3:
                    r, g, b_ch = raw[pos], raw[pos + 1], raw[pos + 2]
                else:
                    r = (raw[pos] << 8) | raw[pos + 1]
                    g = (raw[pos + 2] << 8) | raw[pos + 3]
                    b_ch = (raw[pos + 4] << 8) | raw[pos + 5]
                pos += bpp
                lum = int((299 * r + 587 * g + 114 * b_ch) * scale) // 1000
                row.append(max(0, min(255, lum)))
            luma_rows.append(row)
        if width == 9 and height == 8:
            return _dhash_from_gray_9x8([px for row in luma_rows for px in row])
        return _dhash_from_gray_9x8(_downsample_gray_to_9x8(width, height, luma_rows))

    if magic == b"P5":
        bpp = 1 if maxval < 256 else 2
        raw = data[idx:]
        if len(raw) < width * height * bpp:
            return ""
        luma_rows = []
        pos = 0
        for _y in range(height):
            row = []
            for _x in range(width):
                val = raw[pos] if bpp == 1 else ((raw[pos] << 8) | raw[pos + 1])
                pos += bpp
                row.append(max(0, min(255, int(val * scale))))
            luma_rows.append(row)
        if width == 9 and height == 8:
            return _dhash_from_gray_9x8([px for row in luma_rows for px in row])
        return _dhash_from_gray_9x8(_downsample_gray_to_9x8(width, height, luma_rows))

    if magic in (b"P2", b"P3"):
        nums = [int(x) for x in re.findall(rb"\d+", data[idx:])]
        if magic == b"P2" and len(nums) >= width * height:
            luma_rows = [
                [max(0, min(255, int(nums[y * width + x] * scale))) for x in range(width)]
                for y in range(height)
            ]
            return _dhash_from_gray_9x8(_downsample_gray_to_9x8(width, height, luma_rows))
        if magic == b"P3" and len(nums) >= width * height * 3:
            luma_rows = []
            pos = 0
            for _y in range(height):
                row = []
                for _x in range(width):
                    r, g, b_ch = nums[pos], nums[pos + 1], nums[pos + 2]
                    pos += 3
                    row.append(max(0, min(255, int((299 * r + 587 * g + 114 * b_ch) * scale) // 1000)))
                luma_rows.append(row)
            return _dhash_from_gray_9x8(_downsample_gray_to_9x8(width, height, luma_rows))
    return ""


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def compute_dhash_from_png_bytes(data: bytes) -> str:
    """Pure-stdlib 8-bit non-interlaced PNG decoder for 64-bit dHash fallback."""
    if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ""
    pos = 8
    width = height = bit_depth = color_type = interlace = 0
    idat_parts: list[bytes] = []
    palette: bytes = b""
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        pos += 8
        chunk = data[pos : pos + length]
        pos += length + 4
        if ctype == b"IHDR" and len(chunk) >= 13:
            width, height, bit_depth, color_type, _comp, _filt, interlace = struct.unpack(">IIBBBBB", chunk[:13])
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"IDAT":
            idat_parts.append(chunk)
        elif ctype == b"IEND":
            break
    if width <= 0 or height <= 0 or bit_depth != 8 or interlace != 0 or width * height > 12_000_000:
        return ""
    channels_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_map.get(color_type)
    if not channels:
        return ""
    try:
        raw = zlib.decompress(b"".join(idat_parts))
    except zlib.error:
        return ""
    stride = width * channels
    if len(raw) < height * (stride + 1):
        return ""
    prev = bytearray(stride)
    luma_rows: list[list[int]] = []
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
                scan[i] = (scan[i] + _paeth_predictor(left, up, up_left)) & 0xFF
        elif flt != 0:
            return ""
        prev = scan
        row: list[int] = []
        if color_type in (0, 4):
            for x in range(width):
                row.append(scan[x * channels])
        elif color_type in (2, 6):
            for x in range(width):
                base = x * channels
                row.append((299 * scan[base] + 587 * scan[base + 1] + 114 * scan[base + 2]) // 1000)
        elif color_type == 3 and palette:
            for x in range(width):
                pidx = scan[x] * 3
                if pidx + 2 < len(palette):
                    row.append((299 * palette[pidx] + 587 * palette[pidx + 1] + 114 * palette[pidx + 2]) // 1000)
                else:
                    row.append(0)
        luma_rows.append(row)
    return _dhash_from_gray_9x8(_downsample_gray_to_9x8(width, height, luma_rows))


def image_resize(
    src: str | Path,
    spec: str,
    dest: str | Path,
    *,
    quality: int | None = None,
    timeout: float = 4.0,
    host_root: Path | None = None,
) -> tuple[bool, str]:
    """Resize/convert an image via `alis-theme --image resize` (or ffmpeg/PIL fallback)."""
    src_p = Path(src)
    dest_p = Path(dest)
    if not src_p.is_file():
        return False, "source_missing"

    args = ["resize", str(src_p), str(spec), str(dest_p)]
    if quality is not None:
        args.extend(["--quality", str(max(1, min(100, int(quality))))])
    proc = _run_native(
        "image",
        args,
        direct_binary="alis-theme",
        direct_args=["--image", *args],
        timeout=timeout,
        host_root=host_root,
        allow_in_isolated=True,
    )
    if proc is not None and proc.returncode == 0 and dest_p.is_file() and dest_p.stat().st_size > 0:
        return True, "alis-theme"

    # Optional PIL fallback if installed in a custom Python venv
    try:
        from PIL import Image

        with Image.open(src_p) as im:
            rgb = im.convert("RGB")
            clean = spec.rstrip("!")
            if "x" in clean.lower():
                ws, hs = clean.lower().split("x", 1)
                tw = int(ws) if ws else rgb.width
                th = int(hs) if hs else rgb.height
            else:
                tw = th = int(clean)
            if spec.endswith("!"):
                resized = rgb.resize((max(1, tw), max(1, th)))
            else:
                resized = rgb.copy()
                resized.thumbnail((max(1, tw), max(1, th)))
            ext = dest_p.suffix.lower()
            if ext in (".jpg", ".jpeg"):
                resized.save(dest_p, format="JPEG", quality=int(quality or 80))
            elif ext == ".ppm":
                resized.save(dest_p, format="PPM")
            else:
                resized.save(dest_p, format="PNG")
        if dest_p.is_file() and dest_p.stat().st_size > 0:
            return True, "pil"
    except Exception:
        pass

    return False, "image_resize_unavailable"


def compute_dhash_from_image_file(path: str | Path, host_root: Path | None = None) -> str:
    """Compute 64-bit dHash for any image file without requiring PIL."""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        head = p.read_bytes()[:8]
    except OSError:
        return ""
    if head[:2] in (b"P2", b"P3", b"P5", b"P6", b"P7"):
        try:
            return compute_dhash_from_ppm_bytes(p.read_bytes())
        except OSError:
            return ""

    # Fast path: Rust `alis-theme --image resize <src> 9x8! <tmp.ppm>` -> pure-stdlib PPM parser
    tmp_ppm: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix="alis-dhash-", suffix=".ppm")
        os.close(fd)
        tmp_ppm = Path(name)
        ok, _backend = image_resize(p, "9x8!", tmp_ppm, timeout=2.5, host_root=host_root)
        if ok and tmp_ppm.is_file():
            h = compute_dhash_from_ppm_bytes(tmp_ppm.read_bytes())
            if h:
                return h
    except Exception:
        pass
    finally:
        if tmp_ppm is not None:
            try:
                tmp_ppm.unlink(missing_ok=True)
            except OSError:
                pass

    # Pure-stdlib PNG fallback (works even if alis-theme is absent)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        try:
            return compute_dhash_from_png_bytes(p.read_bytes())
        except OSError:
            return ""
    return ""


def detect_image_mime(data: bytes, fallback: str = "image/png") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def prepare_vlm_image_b64(
    img_path: str | Path,
    *,
    max_dim: int = 1120,
    quality: int = 85,
    host_root: Path | None = None,
) -> tuple[str, str]:
    """Resize/encode an image as base64 JPEG (or raw original bytes fallback) with zero PIL crash."""
    src = Path(img_path)
    if not src.is_file():
        return "", ""
    tmp_jpg: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix="alis-vlm-", suffix=".jpg")
        os.close(fd)
        tmp_jpg = Path(name)
        ok, _backend = image_resize(
            src,
            f"{int(max_dim)}x{int(max_dim)}",
            tmp_jpg,
            quality=quality,
            timeout=4.0,
            host_root=host_root,
        )
        if ok and tmp_jpg.is_file() and tmp_jpg.stat().st_size > 0:
            raw = tmp_jpg.read_bytes()
            return base64.b64encode(raw).decode("ascii"), detect_image_mime(raw, "image/jpeg")
    except Exception:
        pass
    finally:
        if tmp_jpg is not None:
            try:
                tmp_jpg.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        raw = src.read_bytes()
        if raw:
            return base64.b64encode(raw).decode("ascii"), detect_image_mime(raw, "image/png")
    except OSError:
        pass
    return "", ""


# ---------------------------------------------------------------------------
# Wayland screencopy, backlight, clipboard, virtual keyboard, MPRIS2 D-Bus
# ---------------------------------------------------------------------------

def screencopy(
    dest_path: str | Path,
    *,
    output: str = "",
    geometry: str = "",
    scale: float | None = None,
    fmt: str = "png",
    timeout: float = 3.5,
    host_root: Path | None = None,
) -> tuple[bool, str]:
    """Capture Wayland screen via `alis-native screencopy` (fallback: grim, spectacle)."""
    dest = Path(dest_path)
    if not has_wayland_session():
        return False, "no_wayland_display"

    base_args: list[str] = []
    if fmt and fmt != "png":
        base_args.extend(["-t", fmt])
    if scale is not None and scale > 0:
        base_args.extend(["-s", f"{float(scale):.4f}"])
    if geometry:
        base_args.extend(["-g", geometry])

    attempts = (
        [[*base_args, "-o", output, str(dest)], [*base_args, str(dest)]]
        if output
        else [[*base_args, str(dest)]]
    )
    for sc_args in attempts:
        proc = _run_native(
            "screencopy",
            sc_args,
            direct_binary="alis-native",
            direct_args=["screencopy", *sc_args],
            timeout=timeout,
            host_root=host_root,
        )
        if proc is not None and proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True, "alis-native"

    grim_bin = shutil.which("grim")
    if grim_bin and not is_test_or_isolated_env():
        grim_attempts = (
            [[grim_bin, "-o", output, str(dest)], [grim_bin, str(dest)]]
            if output
            else [[grim_bin, str(dest)]]
        )
        for gcmd in grim_attempts:
            try:
                proc = subprocess.run(gcmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
                if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
                    return True, "grim"
            except (OSError, subprocess.SubprocessError):
                pass

    spectacle_bin = shutil.which("spectacle")
    if spectacle_bin and not is_test_or_isolated_env():
        try:
            proc = subprocess.run(
                [spectacle_bin, "-b", "-n", "-o", str(dest)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
                return True, "spectacle"
        except (OSError, subprocess.SubprocessError):
            pass

    return False, "screencopy_unavailable"


def backlight_get(host_root: Path | None = None) -> tuple[bool, int, int, int, str]:
    """Read backlight via `alis-native backlight get` -> (ok, cur, max, pct, label)."""
    proc = _run_native(
        "backlight",
        ["get"],
        direct_binary="alis-native",
        direct_args=["backlight", "get"],
        timeout=3.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        parts = (proc.stdout or b"").decode("utf-8", errors="replace").strip().split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            cur, mx = int(parts[0]), max(1, int(parts[1]))
            pct = round((cur / mx) * 100)
            return True, cur, mx, pct, f"{pct}% ({cur}/{mx})"
    return False, 0, 0, 0, "backlight_unavailable"


def backlight_set(value: str, host_root: Path | None = None) -> tuple[bool, str]:
    """Set backlight via `alis-native backlight set <value>`."""
    target = str(value or "").strip()
    if not target:
        return False, "missing_backlight_value"
    proc = _run_native(
        "backlight",
        ["set", target],
        direct_binary="alis-native",
        direct_args=["backlight", "set", target],
        timeout=3.5,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-native"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "backlight_unavailable"
    return False, err or "backlight_set_failed"


def clipboard_copy(text: str, *, primary: bool = False, host_root: Path | None = None) -> tuple[bool, str]:
    """Copy text to Wayland clipboard via `alis-native clipboard copy`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    args = ["copy"]
    if primary:
        args.append("--primary")
    proc = _run_native(
        "clipboard",
        args,
        direct_binary="alis-native",
        direct_args=["clipboard", *args],
        input_bytes=str(text).encode("utf-8"),
        timeout=3.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-native"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "clipboard_unavailable"
    return False, err or "clipboard_copy_failed"


def clipboard_paste(*, primary: bool = False, host_root: Path | None = None) -> tuple[bool, str]:
    """Read current Wayland clipboard text via `alis-native clipboard paste`."""
    if not has_wayland_session():
        return False, ""
    args = ["paste", "--no-newline"]
    if primary:
        args.append("--primary")
    proc = _run_native(
        "clipboard",
        args,
        direct_binary="alis-native",
        direct_args=["clipboard", *args],
        timeout=2.5,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, (proc.stdout or b"").decode("utf-8", errors="replace")
    return False, ""


def clipboard_paste_image(out_path: Path, *, host_root: Path | None = None) -> tuple[bool, str]:
    """Extract an image MIME type from Wayland clipboard into `out_path` via `alis-native` or `wl-paste`."""
    if is_test_or_isolated_env() or not has_wayland_session():
        return False, "no_wayland_session"
    mime_types: list[str] = []
    list_proc = _run_native(
        "clipboard",
        ["paste", "--list-types"],
        direct_binary="alis-native",
        direct_args=["clipboard", "paste", "--list-types"],
        timeout=2.0,
        host_root=host_root,
    )
    if list_proc is not None and list_proc.returncode == 0:
        mime_types = [ln.strip() for ln in (list_proc.stdout or b"").decode("utf-8", errors="replace").splitlines() if ln.strip()]
    else:
        wl_paste = shutil.which("wl-paste")
        if wl_paste:
            try:
                p = subprocess.run([wl_paste, "--list-types"], capture_output=True, text=True, timeout=2.0, check=False)
                if p.returncode == 0:
                    mime_types = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
            except Exception:
                pass
    chosen_mime = ""
    for cand in ("image/png", "image/jpeg", "image/webp"):
        if cand in mime_types:
            chosen_mime = cand
            break
    if not chosen_mime:
        for mt in mime_types:
            if mt.startswith("image/"):
                chosen_mime = mt
                break
    if not chosen_mime:
        return False, "no_image_in_clipboard"

    raw_bytes = b""
    img_proc = _run_native(
        "clipboard",
        ["paste", "--type", chosen_mime],
        direct_binary="alis-native",
        direct_args=["clipboard", "paste", "--type", chosen_mime],
        timeout=3.5,
        host_root=host_root,
    )
    if img_proc is not None and img_proc.returncode == 0 and img_proc.stdout:
        raw_bytes = bytes(img_proc.stdout)
    elif shutil.which("wl-paste"):
        try:
            p = subprocess.run([shutil.which("wl-paste") or "wl-paste", "--type", chosen_mime], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3.5, check=False)
            if p.returncode == 0 and p.stdout:
                raw_bytes = bytes(p.stdout)
        except Exception:
            pass
    if len(raw_bytes) < 16:
        return False, "clipboard_image_empty"

    ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
    ext = ext_map.get(chosen_mime, ".png")
    dest = Path(out_path).with_suffix(ext)
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_p = dest.with_suffix(f".tmp_{os.getpid()}{ext}")
    tmp_p.write_bytes(raw_bytes)
    try:
        os.chmod(tmp_p, 0o600)
    except OSError:
        pass
    os.replace(tmp_p, dest)
    return True, str(dest)


def has_native_vkeyboard(host_root: Path | None = None) -> bool:
    return bool(find_native_binary("alis-inputd", host_root=host_root) or find_native_dispatch(host_root=host_root))


def input_type(text: str, host_root: Path | None = None) -> tuple[bool, str]:
    """Inject Unicode text on Wayland via `alis-inputd --type <text>`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    if not text:
        return True, "alis-inputd"
    proc = _run_native(
        "input-type",
        [str(text)],
        direct_binary="alis-inputd",
        direct_args=["--type", str(text)],
        timeout=4.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-inputd"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "vkeyboard_unavailable"
    return False, err or "input_type_failed"


def _key_spec_to_inputd_tokens(key_spec: str) -> list[str]:
    """Translate key names or combos (e.g. 'Return', 'ctrl+c', 'ctrl+shift+t') into alis-inputd --key tokens."""
    raw = str(key_spec or "Return").strip()
    if not raw:
        return ["-k", "Return"]
    parts = [p.strip() for p in re.split(r"\+", raw) if p.strip()]
    if len(parts) <= 1:
        return ["-k", raw]
    mods = {"ctrl", "control", "shift", "alt", "super", "win", "logo"}
    tokens: list[str] = []
    for p in parts[:-1]:
        low = p.lower()
        if low in mods:
            tokens.extend(["-M", low])
        else:
            tokens.extend(["-k", p])
    tokens.extend(["-k", parts[-1]])
    return tokens


def input_key(key_spec: str, host_root: Path | None = None) -> tuple[bool, str]:
    """Inject key press/combo on Wayland via `alis-inputd --key`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    tokens = _key_spec_to_inputd_tokens(key_spec)
    proc = _run_native(
        "input-key",
        tokens,
        direct_binary="alis-inputd",
        direct_args=["--key", *tokens],
        timeout=3.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-inputd"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "vkeyboard_unavailable"
    return False, err or "input_key_failed"


_VPOINTER_PROBE_CACHE: dict[str, tuple[float, bool]] = {}
_VPOINTER_PROBE_TTL_SEC = 30.0


def has_native_vpointer(host_root: Path | None = None) -> bool:
    if is_test_or_isolated_env() or not has_wayland_session():
        return False
    bin_id = str(find_native_binary("alis-inputd", host_root=host_root) or find_native_dispatch(host_root=host_root) or "").strip()
    if not bin_id:
        return False
    now_m = time.monotonic()
    cached = _VPOINTER_PROBE_CACHE.get(bin_id)
    if cached is not None and (now_m - cached[0]) < _VPOINTER_PROBE_TTL_SEC:
        return cached[1]
    proc = _run_native(
        "input-pointer",
        ["probe"],
        direct_binary="alis-inputd",
        direct_args=["--pointer", "probe"],
        timeout=1.5,
        host_root=host_root,
    )
    ok = bool(proc is not None and proc.returncode == 0)
    _VPOINTER_PROBE_CACHE[bin_id] = (now_m, ok)
    return ok


def _detect_primary_output_extent(host_root: Path | None = None) -> tuple[int, int]:
    if not is_test_or_isolated_env() and has_wayland_session():
        niri_bin = shutil.which("niri")
        if niri_bin:
            try:
                proc_all = subprocess.run(
                    [niri_bin, "msg", "-j", "outputs"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
                if proc_all.returncode == 0 and proc_all.stdout:
                    outs = json.loads(proc_all.stdout.decode("utf-8", errors="replace"))
                    items = list(outs.values()) if isinstance(outs, dict) else (outs if isinstance(outs, list) else [])
                    max_w = 0
                    max_h = 0
                    for out in items:
                        if not isinstance(out, dict):
                            continue
                        logical = out.get("logical") if isinstance(out.get("logical"), dict) else None
                        if isinstance(logical, dict):
                            lx = int(logical.get("x") or 0)
                            ly = int(logical.get("y") or 0)
                            lw = int(logical.get("width") or 0)
                            lh = int(logical.get("height") or 0)
                            if lw > 0 and lh > 0:
                                max_w = max(max_w, max(0, lx) + lw)
                                max_h = max(max_h, max(0, ly) + lh)
                    if max_w > 0 and max_h > 0:
                        return max_w, max_h
            except Exception:
                pass
            try:
                proc = subprocess.run(
                    [niri_bin, "msg", "-j", "focused-output"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout:
                    data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                    logical = data.get("logical") if isinstance(data, dict) else None
                    if isinstance(logical, dict):
                        lw = int(logical.get("width") or 0)
                        lh = int(logical.get("height") or 0)
                        if lw > 0 and lh > 0:
                            return lw, lh
            except Exception:
                pass
    return 1920, 1080


def _resolve_extent(width: int, height: int, host_root: Path | None = None) -> tuple[int, int]:
    if int(width or 0) > 0 and int(height or 0) > 0:
        return int(width), int(height)
    return _detect_primary_output_extent(host_root=host_root)


def input_pointer_move(
    x: int,
    y: int,
    width: int = 0,
    height: int = 0,
    *,
    host_root: Path | None = None,
) -> tuple[bool, str]:
    """Move Wayland virtual pointer to absolute coordinates via `alis-inputd --pointer move`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    w, h = _resolve_extent(width, height, host_root=host_root)
    px = max(0, min(w, int(x)))
    py = max(0, min(h, int(y)))
    args = ["move", "--x", str(px), "--y", str(py), "--width", str(w), "--height", str(h)]
    proc = _run_native(
        "input-pointer",
        args,
        direct_binary="alis-inputd",
        direct_args=["--pointer", *args],
        timeout=3.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-inputd"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "vpointer_unavailable"
    return False, err or "input_pointer_move_failed"


def input_pointer_click(
    x: int | None = None,
    y: int | None = None,
    width: int = 0,
    height: int = 0,
    *,
    button: str = "left",
    double: bool = False,
    host_root: Path | None = None,
) -> tuple[bool, str]:
    """Click Wayland virtual pointer via `alis-inputd --pointer click`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    btn = str(button or "left").strip().lower()
    if btn not in ("left", "right", "middle"):
        btn = "left"
    args = ["click", "--button", btn]
    if double:
        args.append("--double")
    if x is not None and y is not None:
        w, h = _resolve_extent(width, height, host_root=host_root)
        px = max(0, min(w, int(x)))
        py = max(0, min(h, int(y)))
        args.extend(["--x", str(px), "--y", str(py), "--width", str(w), "--height", str(h)])
    proc = _run_native(
        "input-pointer",
        args,
        direct_binary="alis-inputd",
        direct_args=["--pointer", *args],
        timeout=3.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-inputd"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "vpointer_unavailable"
    return False, err or "input_pointer_click_failed"


def input_pointer_scroll(
    delta: int,
    *,
    axis: str = "vertical",
    x: int | None = None,
    y: int | None = None,
    width: int = 0,
    height: int = 0,
    host_root: Path | None = None,
) -> tuple[bool, str]:
    """Scroll Wayland virtual pointer wheel via `alis-inputd --pointer scroll`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    d = max(-30, min(30, int(delta or 0)))
    if d == 0:
        return True, "alis-inputd"
    ax = "horizontal" if str(axis or "vertical").strip().lower().startswith("h") else "vertical"
    args = ["scroll", "--delta", str(d), "--axis", ax]
    if x is not None and y is not None:
        w, h = _resolve_extent(width, height, host_root=host_root)
        px = max(0, min(w, int(x)))
        py = max(0, min(h, int(y)))
        args.extend(["--x", str(px), "--y", str(py), "--width", str(w), "--height", str(h)])
    proc = _run_native(
        "input-pointer",
        args,
        direct_binary="alis-inputd",
        direct_args=["--pointer", *args],
        timeout=3.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-inputd"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "vpointer_unavailable"
    return False, err or "input_pointer_scroll_failed"


def input_pointer_drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int = 0,
    height: int = 0,
    *,
    button: str = "left",
    steps: int = 10,
    host_root: Path | None = None,
) -> tuple[bool, str]:
    """Drag Wayland virtual pointer from (x1, y1) to (x2, y2) via `alis-inputd --pointer drag`."""
    if not has_wayland_session():
        return False, "no_wayland_display"
    w, h = _resolve_extent(width, height, host_root=host_root)
    sx = max(0, min(w, int(x1)))
    sy = max(0, min(h, int(y1)))
    ex = max(0, min(w, int(x2)))
    ey = max(0, min(h, int(y2)))
    btn = str(button or "left").strip().lower()
    if btn not in ("left", "right", "middle"):
        btn = "left"
    st = max(2, min(24, int(steps or 10)))
    args = [
        "drag",
        "--x1", str(sx),
        "--y1", str(sy),
        "--x2", str(ex),
        "--y2", str(ey),
        "--width", str(w),
        "--height", str(h),
        "--button", btn,
        "--steps", str(st),
    ]
    proc = _run_native(
        "input-pointer",
        args,
        direct_binary="alis-inputd",
        direct_args=["--pointer", *args],
        timeout=4.0,
        host_root=host_root,
    )
    if proc is not None and proc.returncode == 0:
        return True, "alis-inputd"
    err = (proc.stderr or b"").decode("utf-8", errors="replace").strip() if proc is not None else "vpointer_unavailable"
    return False, err or "input_pointer_drag_failed"


def _list_mpris_bus_names(timeout: float = 2.0) -> list[str]:
    busctl = shutil.which("busctl")
    if busctl:
        try:
            proc = subprocess.run(
                [busctl, "--user", "list", "--no-legend", "--no-pager"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0:
                names = []
                for ln in (proc.stdout or "").splitlines():
                    parts = ln.split()
                    if parts and parts[0].startswith("org.mpris.MediaPlayer2."):
                        names.append(parts[0])
                return names
        except (OSError, subprocess.SubprocessError):
            pass
    gdbus = shutil.which("gdbus")
    if gdbus:
        try:
            proc = subprocess.run(
                [gdbus, "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ListNames"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0:
                return re.findall(r"org\.mpris\.MediaPlayer2\.[A-Za-z0-9_.-]+", proc.stdout or "")
        except (OSError, subprocess.SubprocessError):
            pass
    return []


def _read_mpris_status(bus_name: str, timeout: float = 2.0) -> dict:
    info = {"player": bus_name.removeprefix("org.mpris.MediaPlayer2."), "status": "Unknown", "title": "", "artist": ""}
    busctl = shutil.which("busctl")
    if not busctl:
        return info
    try:
        st_proc = subprocess.run(
            [busctl, "--user", "get-property", bus_name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "PlaybackStatus"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        if st_proc.returncode == 0:
            m = re.search(r'"([^"]+)"', st_proc.stdout or "")
            if m:
                info["status"] = m.group(1)
        md_proc = subprocess.run(
            [busctl, "--user", "--json=short", "get-property", bus_name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Metadata"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        if md_proc.returncode == 0 and md_proc.stdout.strip():
            parsed = json.loads(md_proc.stdout)
            data = parsed.get("data") if isinstance(parsed, dict) else None
            if isinstance(data, dict):
                t_val = data.get("xesam:title")
                if isinstance(t_val, dict):
                    info["title"] = str(t_val.get("data") or "")
                a_val = data.get("xesam:artist")
                if isinstance(a_val, dict):
                    ad = a_val.get("data")
                    if isinstance(ad, list):
                        info["artist"] = ", ".join(str(x) for x in ad if x)
                    elif ad:
                        info["artist"] = str(ad)
    except Exception:
        pass
    return info


def mpris_media_control(action: str = "play-pause", timeout: float = 2.5) -> tuple[bool, str, dict]:
    """Control or inspect active MPRIS2 media players directly over session D-Bus (no playerctl needed)."""
    if is_test_or_isolated_env():
        return False, "No active MPRIS media player (isolated test environment).", {}
    players = _list_mpris_bus_names(timeout=timeout)
    if not players:
        return False, "No active MPRIS media player found on session D-Bus.", {}

    # Prefer a currently Playing player if multiple exist
    chosen = players[0]
    chosen_meta = _read_mpris_status(chosen, timeout=timeout)
    if chosen_meta.get("status") != "Playing" and len(players) > 1:
        for cand in players[1:6]:
            cm = _read_mpris_status(cand, timeout=timeout)
            if cm.get("status") == "Playing":
                chosen = cand
                chosen_meta = cm
                break

    sub = str(action or "status").strip().lower()
    method_map = {
        "play": "Play",
        "pause": "Pause",
        "play-pause": "PlayPause",
        "next": "Next",
        "previous": "Previous",
        "stop": "Stop",
    }
    if sub != "status":
        method = method_map.get(sub, "PlayPause")
        busctl = shutil.which("busctl")
        gdbus = shutil.which("gdbus")
        called = False
        if busctl:
            proc = subprocess.run(
                [busctl, "--user", "call", chosen, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", method],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            called = proc.returncode == 0
        elif gdbus:
            proc = subprocess.run(
                [gdbus, "call", "--session", "--dest", chosen, "--object-path", "/org/mpris/MediaPlayer2", "--method", f"org.mpris.MediaPlayer2.Player.{method}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            called = proc.returncode == 0
        if not called:
            return False, f"Failed to send MPRIS '{method}' to {chosen_meta['player']}.", chosen_meta
        chosen_meta = _read_mpris_status(chosen, timeout=timeout)

    title = chosen_meta.get("title") or ""
    artist = chosen_meta.get("artist") or ""
    track = f"{artist} - {title}".strip(" -") if (artist or title) else chosen_meta["player"]
    line = f"{chosen_meta['player']}: {track} ({chosen_meta['status']})"
    return True, line, chosen_meta
