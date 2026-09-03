from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from .device import DeviceController


@dataclass
class ScreenshotCollector:
    output_dir: Path = Path("datasets/raw")

    def capture(self, device: DeviceController, tag: str = "sample") -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_tag = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in tag)
        image_path = self.output_dir / f"{device.serial.replace(':', '_')}_{safe_tag}_{stamp}.png"
        saved = device.screenshot(image_path)
        self._append_manifest(saved, device.serial, safe_tag)
        return saved

    def _append_manifest(self, image_path: Path, serial: str, tag: str) -> None:
        manifest_path = self.output_dir / "manifest.jsonl"
        record = {
            "path": str(image_path),
            "serial": serial,
            "tag": tag,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        with manifest_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
