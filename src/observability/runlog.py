from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import PROJECT_ROOT
from src.models import StrategyDecision

LOGS_DIR = PROJECT_ROOT / "logs"
INGEST_RUN_LOG = LOGS_DIR / "ingest_run.json"


def ensure_logs_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def write_ingest_run_log(
    decision: Optional[StrategyDecision],
    stage_summary: Dict[str, Any],
    audit: Optional[Dict[str, Any]] = None,
    duration_s: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
    path: Optional[Path] = None,
) -> Path:
    target = path or INGEST_RUN_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": "ingestion",
        "decision": decision.to_dict() if decision is not None else None,
        "stage_summary": stage_summary,
        "audit": audit,
        "duration_s": round(duration_s, 3) if duration_s is not None else None,
    }
    if extra:
        payload.update(extra)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def read_ingest_run_log() -> Dict[str, Any]:
    if not INGEST_RUN_LOG.exists():
        return {}
    return json.loads(INGEST_RUN_LOG.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


__all__ = [
    "INGEST_RUN_LOG",
    "LOGS_DIR",
    "ensure_logs_dir",
    "read_ingest_run_log",
    "read_jsonl",
    "write_ingest_run_log",
    "write_jsonl",
]
