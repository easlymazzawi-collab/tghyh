from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.config import ROOT_DIR, app_config


class ResultLogger:
    def __init__(self) -> None:
        self.log_dir = ROOT_DIR / app_config.log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write(self, job_id: str, entry: Dict[str, Any]) -> None:
        path = self.log_dir / f"{job_id}.jsonl"
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **entry,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


result_logger = ResultLogger()
