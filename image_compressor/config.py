"""读写本机 TinyPNG API Key，不进入仓库。"""

import json
import os
from pathlib import Path

def config_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "python-desktop-tools" / "tinypng.json"


def load_api_key() -> str:
    path = config_path()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ""
    key = data.get("api_key", "") if isinstance(data, dict) else ""
    return key.strip() if isinstance(key, str) else ""


def save_api_key(api_key: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": api_key.strip()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
