from __future__ import annotations

import os
from pathlib import Path


TEST_DATA_DIR = Path(".test-data")
TEST_DATA_DIR.mkdir(exist_ok=True)

os.environ.setdefault(
    "TVMANAGER_DATABASE_URL",
    f"sqlite+aiosqlite:///{(TEST_DATA_DIR / 'tvmanager-test.db').as_posix()}",
)
os.environ.setdefault("TVMANAGER_SECRET_KEY", "tvmanager-ci-test-secret-key")
os.environ.setdefault("TVMANAGER_SQL_ECHO", "0")
