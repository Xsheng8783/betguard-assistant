"""Pytest configuration — enables legacy HMAC licenses for test suite only."""

import os

os.environ.setdefault("BETGUARD_ALLOW_LEGACY_LICENSES", "1")
