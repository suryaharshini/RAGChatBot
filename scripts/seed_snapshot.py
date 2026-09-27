from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.stage1_load import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
