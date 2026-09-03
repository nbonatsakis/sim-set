#!/usr/bin/env python3
"""simset: project-scoped iOS simulator sets for concurrent coding agents."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simsetlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
