from __future__ import annotations

import os
from pathlib import Path


TEST_DATA_DIR = Path(".test-data")
TEST_DATABASE_FILE = TEST_DATA_DIR / "tvmanager-test.db"
TEST_DATA_DIR.mkdir(exist_ok=True)

# Tests must never touch the developer or production database.
os.environ.setdefault(
    "TVMANAGER_DATABASE_URL",
    f"sqlite+aiosqlite:///{TEST_DATABASE_FILE.as_posix()}",
)
os.environ.setdefault("TVMANAGER_SECRET_KEY", "tvmanager-ci-test-secret-key")
os.environ.setdefault("TVMANAGER_SQL_ECHO", "0")


def pytest_sessionstart() -> None:
    """Start every test run with a clean, isolated SQLite database."""
    TEST_DATABASE_FILE.unlink(missing_ok=True)


def pytest_sessionfinish() -> None:
    """Remove test state so it cannot be mistaken for application data."""
    TEST_DATABASE_FILE.unlink(missing_ok=True)
