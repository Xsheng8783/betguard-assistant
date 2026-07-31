"""Pytest configuration — enables legacy HMAC licenses + Ed25519 test key."""

import os

os.environ.setdefault("BETGUARD_ALLOW_LEGACY_LICENSES", "1")
os.environ.setdefault("BETGUARD_LICENSE_PRIVATE_KEY",
                       "mrFXDQsGxQrGHf5fk+CY3eC00IA/DQdZq/OanLru50I=")
