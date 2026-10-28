"""Shared fixtures."""

import sys
from pathlib import Path

import pytest

# Make the in-repo package importable when running from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipmind.config import Config  # noqa: E402
from clipmind.store import ClipStore  # noqa: E402


@pytest.fixture()
def cfg(tmp_path) -> Config:
    return Config(db_path=tmp_path / "test.db")


@pytest.fixture()
def store(cfg) -> ClipStore:
    return ClipStore(cfg.db_path)
