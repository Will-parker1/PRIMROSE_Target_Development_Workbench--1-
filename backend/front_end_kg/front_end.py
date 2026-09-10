"""Compatibility launcher for the web application.

Prefer ``python server.py`` from the project root. This file remains so older
scripts that referenced ``front_end_kg/front_end.py`` still work.
"""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import main  # noqa: E402


if __name__ == "__main__":
    main()
