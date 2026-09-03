from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class EmulatorConfig:
    name: str
    serial: str
    enabled: bool = True


@dataclass(frozen=True)
class TargetConfig:
    name: str
    target_type: str = "adb"
    enabled: bool = True
    serial: str = ""
    hwnd: int | None = None
    window_title: str | None = None
    process_name: str | None = None
    match_index: int | None = None
    game_path: Path | None = None
    local_ip: str | None = None

    @property
    def identifier(self) -> str:
        if self.target_type == "adb":
            return self.serial
        if self.hwnd is not None:
            return f"hwnd:{self.hwnd}"
        if self.window_title:
            return self.window_title
        if self.process_name:
            return self.process_name
        return self.name


@dataclass(frozen=True)
class RuntimeConfig:
    max_workers: int = 1
    screenshot_dir: Path = Path("datasets/raw")
    artifact_dir: Path = Path("runs")
    default_confidence: float = 0.55


@dataclass(frozen=True)
class BotConfig:
    runtime: RuntimeConfig
    emulators: tuple[EmulatorConfig, ...]
    targets: tuple[TargetConfig, ...] = ()

    @property
    def enabled_emulators(self) -> tuple[EmulatorConfig, ...]:
        return tuple(item for item in self.emulators if item.enabled)

    @property
    def enabled_targets(self) -> tuple[TargetConfig, ...]:
        explicit_targets = tuple(item for item in self.targets if item.enabled)
        if explicit_targets:
            return explicit_targets
        legacy_targets = tuple(
            TargetConfig(
                name=item.name,
                target_type="adb",
                enabled=item.enabled,
                serial=item.serial,
            )
            for item in self.enabled_emulators
        )
        return legacy_targets


def load_config(path: str | Path) -> BotConfig:
    config_path = Path(path)
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    runtime_raw = raw.get("runtime", {})
    runtime = RuntimeConfig(
        max_workers=int(runtime_raw.get("max_workers", 1)),
        screenshot_dir=Path(runtime_raw.get("screenshot_dir", "datasets/raw")),
        artifact_dir=Path(runtime_raw.get("artifact_dir", "runs")),
        default_confidence=float(runtime_raw.get("default_confidence", 0.55)),
    )

    emulators = tuple(
        EmulatorConfig(
            name=str(item["name"]),
            serial=str(item["serial"]),
            enabled=bool(item.get("enabled", True)),
        )
        for item in raw.get("emulators", [])
    )

    targets = tuple(_load_target(item) for item in raw.get("targets", []))

    return BotConfig(runtime=runtime, emulators=emulators, targets=targets)


def _load_target(item: dict) -> TargetConfig:
    target_type = str(item.get("type", item.get("target_type", "adb"))).lower()
    hwnd_raw = item.get("hwnd")
    match_index_raw = item.get("match_index")
    game_path_raw = item.get("game_path")
    return TargetConfig(
        name=str(item["name"]),
        target_type=target_type,
        enabled=bool(item.get("enabled", True)),
        serial=str(item.get("serial", "")),
        hwnd=int(hwnd_raw) if hwnd_raw is not None else None,
        window_title=_optional_str(item.get("window_title")),
        process_name=_optional_str(item.get("process_name")),
        match_index=int(match_index_raw) if match_index_raw is not None else None,
        game_path=Path(game_path_raw) if game_path_raw else None,
        local_ip=_optional_str(item.get("local_ip")),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
