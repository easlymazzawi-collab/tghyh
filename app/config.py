from __future__ import annotations

import json
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.json"


class AppConfig(BaseModel):
    allowed_domains: List[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    default_rate_limit_rps: float = 2.0
    default_max_workers: int = 3
    max_credentials_per_job: int = 100
    log_dir: str = "logs"


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    raw = CONFIG_PATH.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "\n=== LOI FILE config.json ===\n"
            f"Dong {exc.lineno}, cot {exc.colno}: {exc.msg}\n"
            f"File: {CONFIG_PATH}\n\n"
            "Cach sua nhanh:\n"
            "  1. Chay: python run.py --fix-config\n"
            "  2. Hoac sua tay — moi domain phai co dau phay, vi du:\n"
            '     "localhost",\n'
            '     "127.0.0.1",\n'
            '     "panel.cloudzy.com"\n'
        ) from exc
    return AppConfig(**data)


def repair_config_file() -> Path:
    backup: Path | None = None
    if CONFIG_PATH.exists():
        backup = CONFIG_PATH.with_suffix(".json.bak")
        counter = 0
        while backup.exists():
            counter += 1
            backup = CONFIG_PATH.with_name(f"config.json.bak{counter}")
        CONFIG_PATH.replace(backup)

    default = AppConfig()
    CONFIG_PATH.write_text(
        json.dumps(default.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return backup if backup else CONFIG_PATH


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080
    api_key: str = ""

    class Config:
        env_prefix = "LOGIN_TESTER_"


settings = Settings()
app_config = load_config()
