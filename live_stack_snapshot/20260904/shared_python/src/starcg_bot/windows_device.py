from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
PW_CLIENTONLY = 0x00000001
SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", wintypes.BYTE),
        ("rgbGreen", wintypes.BYTE),
        ("rgbRed", wintypes.BYTE),
        ("rgbReserved", wintypes.BYTE),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    process_path: str | None = None

    @property
    def process_name(self) -> str:
        if not self.process_path:
            return ""
        return Path(self.process_path).name

    def to_dict(self) -> dict[str, object]:
        return {
            "hwnd": self.hwnd,
            "hwnd_hex": f"0x{self.hwnd:X}",
            "pid": self.pid,
            "title": self.title,
            "class_name": self.class_name,
            "process_name": self.process_name,
            "process_path": self.process_path,
        }


_API_CONFIGURED = False


def parse_hwnd(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = value.strip()
    if not text:
        return None
    return int(text, 16 if text.lower().startswith("0x") else 10)


def list_windows(
    title_contains: str | None = None,
    process_name: str | None = None,
    process_path: str | Path | None = None,
    process_path_prefix: str | Path | None = None,
    visible_only: bool = True,
) -> list[WindowInfo]:
    user32, _, _ = _winapi()
    wanted_title = title_contains.casefold() if title_contains else None
    wanted_process = process_name.casefold() if process_name else None
    wanted_path = _normalize_path(process_path) if process_path else None
    wanted_prefix = _normalize_path(process_path_prefix) if process_path_prefix else None
    items: list[WindowInfo] = []

    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    def callback(hwnd: int, _lparam: int) -> bool:
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        info = _get_window_info(hwnd)
        if not info.title and not info.class_name:
            return True
        if wanted_title and wanted_title not in info.title.casefold():
            return True
        if wanted_process and not _matches_process(info.process_name, wanted_process):
            return True
        if wanted_path and _normalize_path(info.process_path) != wanted_path:
            return True
        if wanted_prefix:
            actual_path = _normalize_path(info.process_path)
            if not actual_path or not _is_path_under(actual_path, wanted_prefix):
                return True
        items.append(info)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return items


def resolve_window(
    hwnd: int | str | None = None,
    title_contains: str | None = None,
    process_name: str | None = None,
    process_path: str | Path | None = None,
    process_path_prefix: str | Path | None = None,
    match_index: int | None = None,
) -> WindowInfo:
    parsed_hwnd = parse_hwnd(hwnd)
    if parsed_hwnd is not None:
        user32, _, _ = _winapi()
        if not user32.IsWindow(parsed_hwnd):
            raise RuntimeError(f"window handle does not exist: {parsed_hwnd}")
        info = _get_window_info(parsed_hwnd)
        _validate_window_path(info, process_path=process_path, process_path_prefix=process_path_prefix)
        return info

    matches = list_windows(
        title_contains=title_contains,
        process_name=process_name,
        process_path=process_path,
        process_path_prefix=process_path_prefix,
    )
    if match_index is not None:
        if match_index < 1:
            raise RuntimeError("match_index is 1-based and must be >= 1")
        if match_index > len(matches):
            raise RuntimeError(f"match_index {match_index} is out of range for {len(matches)} matched windows")
        return matches[match_index - 1]
    if not matches:
        raise RuntimeError("no matching window found")
    if len(matches) > 1:
        preview = ", ".join(f"0x{item.hwnd:X} pid={item.pid} title={item.title!r}" for item in matches[:8])
        raise RuntimeError(f"multiple matching windows found; pass --hwnd or --match-index. Matches: {preview}")
    return matches[0]


def client_size(hwnd: int | str) -> tuple[int, int]:
    parsed_hwnd = parse_hwnd(hwnd)
    if parsed_hwnd is None:
        raise RuntimeError("client_size requires a window handle")
    return _client_size(parsed_hwnd)


def enable_physical_dpi_coordinates() -> None:
    """Keep Win32 geometry in physical pixels for the current playback thread."""
    if os.name != "nt":
        raise RuntimeError("physical DPI coordinates require Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    setter = user32.SetThreadDpiAwarenessContext
    setter.argtypes = [ctypes.c_void_p]
    setter.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    if not setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        raise RuntimeError(
            f"could not enable physical DPI coordinates: WinError {ctypes.get_last_error()}"
        )


def screen_to_client(hwnd: int | str, x: int, y: int) -> tuple[int, int]:
    parsed_hwnd = parse_hwnd(hwnd)
    if parsed_hwnd is None:
        raise RuntimeError("screen_to_client requires a window handle")
    user32, _, _ = _winapi()
    point = POINT(int(x), int(y))
    if not user32.ScreenToClient(parsed_hwnd, ctypes.byref(point)):
        raise RuntimeError(f"ScreenToClient failed for hwnd=0x{parsed_hwnd:X}")
    return int(point.x), int(point.y)


@dataclass
class WindowsWindowController:
    hwnd: int
    serial: str = ""

    def __post_init__(self) -> None:
        info = resolve_window(hwnd=self.hwnd)
        self.hwnd = info.hwnd
        if not self.serial:
            self.serial = f"hwnd-0x{self.hwnd:X}"

    @classmethod
    def from_locator(
        cls,
        *,
        hwnd: int | str | None = None,
        title_contains: str | None = None,
        process_name: str | None = None,
        process_path: str | Path | None = None,
        process_path_prefix: str | Path | None = None,
        match_index: int | None = None,
        serial: str | None = None,
    ) -> "WindowsWindowController":
        info = resolve_window(
            hwnd=hwnd,
            title_contains=title_contains,
            process_name=process_name,
            process_path=process_path,
            process_path_prefix=process_path_prefix,
            match_index=match_index,
        )
        return cls(hwnd=info.hwnd, serial=serial or "")

    def screenshot(self, path: str | Path) -> Path:
        from PIL import Image

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        width, height = _client_size(self.hwnd)
        if width <= 0 or height <= 0:
            raise RuntimeError(f"window has empty client area: hwnd=0x{self.hwnd:X}")

        raw = _capture_client_bgra(self.hwnd, width, height)
        image = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1).convert("RGB")
        image.save(target)
        return target

    def tap(self, x: int, y: int) -> None:
        _post_mouse(self.hwnd, WM_MOUSEMOVE, 0, x, y)
        _post_mouse(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, x, y)
        _post_mouse(self.hwnd, WM_LBUTTONUP, 0, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float = 0.3) -> None:
        steps = max(2, min(24, int(duration_sec * 30)))
        _post_mouse(self.hwnd, WM_MOUSEMOVE, 0, x1, y1)
        _post_mouse(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, x1, y1)
        sleep_sec = max(0.0, duration_sec) / steps
        for step in range(1, steps):
            ratio = step / steps
            x = round(x1 + (x2 - x1) * ratio)
            y = round(y1 + (y2 - y1) * ratio)
            _post_mouse(self.hwnd, WM_MOUSEMOVE, MK_LBUTTON, x, y)
            if sleep_sec:
                time.sleep(sleep_sec)
        _post_mouse(self.hwnd, WM_MOUSEMOVE, MK_LBUTTON, x2, y2)
        _post_mouse(self.hwnd, WM_LBUTTONUP, 0, x2, y2)

    def keyevent(self, key: str) -> None:
        vk = _virtual_key(key)
        _post_message(self.hwnd, WM_KEYDOWN, vk, 0)
        _post_message(self.hwnd, WM_KEYUP, vk, 0xC0000001)

    def text(self, text: str) -> None:
        for char in text:
            _post_message(self.hwnd, WM_CHAR, ord(char), 0)


def _matches_process(actual_name: str, wanted_casefold: str) -> bool:
    actual = actual_name.casefold()
    if not actual:
        return False
    wanted_stem = wanted_casefold[:-4] if wanted_casefold.endswith(".exe") else wanted_casefold
    actual_stem = actual[:-4] if actual.endswith(".exe") else actual
    return actual == wanted_casefold or actual_stem == wanted_stem


def _normalize_path(path: str | Path | None) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(os.fspath(path))).rstrip("\\/")


def _is_path_under(path: str, prefix: str) -> bool:
    if path == prefix:
        return True
    return path.startswith(prefix + os.sep)


def _validate_window_path(
    info: WindowInfo,
    *,
    process_path: str | Path | None,
    process_path_prefix: str | Path | None,
) -> None:
    wanted_path = _normalize_path(process_path) if process_path else ""
    wanted_prefix = _normalize_path(process_path_prefix) if process_path_prefix else ""
    actual_path = _normalize_path(info.process_path)
    if wanted_path and actual_path != wanted_path:
        raise RuntimeError(
            f"window path mismatch: hwnd=0x{info.hwnd:X} actual={info.process_path!r} expected={os.fspath(process_path)!r}"
        )
    if wanted_prefix and (not actual_path or not _is_path_under(actual_path, wanted_prefix)):
        raise RuntimeError(
            f"window is outside allowed prefix: hwnd=0x{info.hwnd:X} actual={info.process_path!r} prefix={os.fspath(process_path_prefix)!r}"
        )


def _winapi():
    if os.name != "nt":
        raise RuntimeError("Windows window targets require Windows")
    _configure_api()
    return ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32


def _configure_api() -> None:
    global _API_CONFIGURED
    if _API_CONFIGURED:
        return
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ScreenToClient.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
    user32.VkKeyScanW.restype = ctypes.c_short

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    _API_CONFIGURED = True


def _get_window_info(hwnd: int) -> WindowInfo:
    user32, _, _ = _winapi()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        title=_window_text(hwnd),
        class_name=_class_name(hwnd),
        process_path=_process_path(int(pid.value)),
    )


def _window_text(hwnd: int) -> str:
    user32, _, _ = _winapi()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class_name(hwnd: int) -> str:
    user32, _, _ = _winapi()
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _process_path(pid: int) -> str | None:
    if pid <= 0:
        return None
    _, _, kernel32 = _winapi()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def _client_size(hwnd: int) -> tuple[int, int]:
    user32, _, _ = _winapi()
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError(f"GetClientRect failed for hwnd=0x{hwnd:X}")
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def _capture_client_bgra(hwnd: int, width: int, height: int) -> bytes:
    user32, gdi32, _ = _winapi()
    window_dc = user32.GetDC(hwnd)
    if not window_dc:
        raise RuntimeError(f"GetDC failed for hwnd=0x{hwnd:X}")
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not mem_dc or not bitmap:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, window_dc)
        raise RuntimeError("failed to allocate capture bitmap")

    old_obj = gdi32.SelectObject(mem_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY):
            if not gdi32.BitBlt(mem_dc, 0, 0, width, height, window_dc, 0, 0, SRCCOPY):
                raise RuntimeError(f"PrintWindow and BitBlt failed for hwnd=0x{hwnd:X}")
        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(bitmap_info), DIB_RGB_COLORS)
        if rows == 0:
            raise RuntimeError(f"GetDIBits failed for hwnd=0x{hwnd:X}")
        return buffer.raw
    finally:
        if old_obj:
            gdi32.SelectObject(mem_dc, old_obj)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, window_dc)


def _post_mouse(hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
    _post_message(hwnd, message, wparam, _make_lparam(x, y))


def _post_message(hwnd: int, message: int, wparam: int, lparam: int) -> None:
    user32, _, _ = _winapi()
    if not user32.PostMessageW(hwnd, message, wparam, lparam):
        raise RuntimeError(f"PostMessage failed for hwnd=0x{hwnd:X}, message=0x{message:X}")


def _make_lparam(x: int, y: int) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _virtual_key(key: str) -> int:
    text = key.strip()
    if not text:
        raise RuntimeError("empty keyevent")
    aliases = {
        "BACK": 0x08,
        "BACKSPACE": 0x08,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "RETURN": 0x0D,
        "SHIFT": 0x10,
        "CTRL": 0x11,
        "CONTROL": 0x11,
        "ALT": 0x12,
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "SPACE": 0x20,
        "LEFT": 0x25,
        "UP": 0x26,
        "RIGHT": 0x27,
        "DOWN": 0x28,
        "DELETE": 0x2E,
    }
    upper = text.upper()
    if upper in aliases:
        return aliases[upper]
    if upper.startswith("VK_"):
        return int(upper[3:], 16)
    if len(text) == 1:
        user32, _, _ = _winapi()
        value = user32.VkKeyScanW(text)
        if value != -1:
            return value & 0xFF
        return ord(upper)
    raise RuntimeError(f"unsupported keyevent for Windows target: {key!r}")
