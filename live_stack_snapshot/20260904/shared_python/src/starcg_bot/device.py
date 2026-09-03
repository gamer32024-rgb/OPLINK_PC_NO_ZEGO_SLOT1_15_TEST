from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class DeviceController(Protocol):
    serial: str

    def screenshot(self, path: str | Path) -> Path:
        ...

    def tap(self, x: int, y: int) -> None:
        ...

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float = 0.3) -> None:
        ...

    def keyevent(self, key: str) -> None:
        ...


@dataclass
class AdbDeviceController:
    serial: str

    def __post_init__(self) -> None:
        try:
            from adbutils import adb
        except ImportError as exc:
            raise RuntimeError("adbutils is required for real device control. Install with `python -m pip install -e .`.") from exc
        self._device = adb.device(serial=self.serial)

    def screenshot(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        image = self._device.screenshot()
        image.save(target)
        return target

    def tap(self, x: int, y: int) -> None:
        self._device.click(int(x), int(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float = 0.3) -> None:
        self._device.swipe(int(x1), int(y1), int(x2), int(y2), float(duration_sec))

    def keyevent(self, key: str) -> None:
        self._device.keyevent(key)


def list_adb_devices() -> list[str]:
    try:
        from adbutils import adb
    except ImportError as exc:
        raise RuntimeError("adbutils is required to list devices. Install with `python -m pip install -e .`.") from exc
    return [device.serial for device in adb.device_list()]
