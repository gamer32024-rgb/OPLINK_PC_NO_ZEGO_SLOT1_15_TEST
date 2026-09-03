from __future__ import annotations

import json
from pathlib import Path
from typing import Any


JSON_FILE_ENCODING = "utf-8-sig"


def json_text(data: Any, *, indent: int = 2) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False)


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding=JSON_FILE_ENCODING) as file:
        return json.load(file)


def write_json_file(path: Path, data: Any, *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(data, indent=indent) + "\n", encoding=JSON_FILE_ENCODING)
