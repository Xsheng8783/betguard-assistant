"""
Local license system — device-bound activation codes with HMAC verification.

First edition: trial_7d / trial_30d plans, no cloud, no payment.

Storage: get_data_dir()/license.json

Activation code format v2 (compact):
  Payload: [plan_byte(1)][expires_epoch_day(2)][device_hash_prefix(4)] = 7 bytes
  HMAC-SHA256(payload + device_hash, secret), truncated to 6 bytes
  Encoded as base32hex (RFC 4648 §7, 0-9 A-V uppercase)
  Prefix BG7- for trial_7d, BG30- for trial_30d
  Dashes every 4 chars for readability
  Example: BG7-XXXX-XXXX-XXXX-XXXX-XX (22 chars)

v1 compatibility (long hex codes): still supported via fallback in verify_activation_code.
"""

import hashlib
import hmac
import json
import os
import struct
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from betguard.user_data import get_data_dir

_LICENSE_SECRET = os.environ.get("BETGUARD_LICENSE_SECRET", "betguard-local-license-secret-v1")
_LICENSE_FILE = os.path.join(get_data_dir(), "license.json")

# Epoch for compact date encoding (days since this date)
_EPOCH = date(2024, 1, 1)

PLANS = {"trial_7d": 7, "trial_30d": 30}
_PLAN_BYTES = {"trial_7d": 0x07, "trial_30d": 0x1E}
_PLAN_REVERSE = {0x07: "trial_7d", 0x1E: "trial_30d"}

# Crockford base32 alphabet (no I,L,O,U to avoid confusion)
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_B32_DECODE = {c: i for i, c in enumerate(_B32)}


def _b32_encode(data: bytes) -> str:
    """Encode bytes to base32hex (Crockford-like, uppercase)."""
    bits = 0
    bit_count = 0
    result = []
    for byte in data:
        bits = (bits << 8) | byte
        bit_count += 8
        while bit_count >= 5:
            bit_count -= 5
            result.append(_B32[(bits >> bit_count) & 0x1F])
    if bit_count > 0:
        result.append(_B32[(bits << (5 - bit_count)) & 0x1F])
    return ''.join(result)


def _b32_decode(s: str) -> bytes:
    """Decode base32hex string to bytes."""
    s = s.upper().replace('-', '')
    bits = 0
    bit_count = 0
    result = bytearray()
    for ch in s:
        if ch not in _B32_DECODE:
            raise ValueError(f"Invalid base32 char: {ch}")
        bits = (bits << 5) | _B32_DECODE[ch]
        bit_count += 5
        while bit_count >= 8:
            bit_count -= 8
            result.append((bits >> bit_count) & 0xFF)
    return bytes(result)


def _machine_id() -> str:
    """Stable per-machine identifier. Not a secret — just for device binding."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            return winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        pass
    import platform
    raw = platform.node() + str(uuid.getnode())
    return hashlib.sha256(raw.encode()).hexdigest()


def get_device_id() -> str:
    """Short displayable device code (prefix of SHA-256 hash)."""
    h = hashlib.sha256(_machine_id().encode()).hexdigest().upper()
    return f"BG-{h[:4]}-{h[4:8]}-{h[8:12]}"


def _device_id_hash() -> str:
    """Full hash used internally for license binding."""
    return hashlib.sha256(_machine_id().encode()).hexdigest()


def _sign(payload: dict) -> str:
    """HMAC-SHA256 sign the sorted JSON payload (for license.json storage)."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hmac.new(_LICENSE_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _verify_signature(payload: dict, signature: str) -> bool:
    return hmac.compare_digest(_sign(payload), signature)


def _hmac_sign_compact(data: bytes) -> bytes:
    """HMAC-SHA256 sign compact binary data, truncated to 6 bytes."""
    full = hmac.new(_LICENSE_SECRET.encode(), data, hashlib.sha256).digest()
    return full[:6]


def _hmac_verify_compact(data: bytes, sig: bytes) -> bool:
    return hmac.compare_digest(_hmac_sign_compact(data), sig)


# ── Compact activation code (v2) ──

def _issue_compact(device_hash: str, days: int, plan: str) -> str:
    """Generate a compact (v2) activation code."""
    plan_byte = _PLAN_BYTES[plan]
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    epoch_day = (expires.date() - _EPOCH).days
    dev_prefix = bytes.fromhex(device_hash)[:4]

    payload = struct.pack(">BH4s", plan_byte, epoch_day, dev_prefix)
    sig = _hmac_sign_compact(payload + bytes.fromhex(device_hash))
    encoded = _b32_encode(payload + sig)

    # Group in 4s with dashes
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    prefix = "BG7-" if plan == "trial_7d" else "BG30-"
    return prefix + "-".join(chunks)


def _verify_compact(raw: bytes, current_device_hash: str) -> dict | None:
    """Try to verify a compact (v2) activation code. Returns None if not v2 format."""
    if len(raw) < 8:
        return None
    # v2: payload(7) + sig(6) = 13 bytes
    if len(raw) != 13:
        return None
    payload = raw[:7]
    sig = raw[7:]

    try:
        plan_byte, epoch_day, dev_prefix = struct.unpack(">BH4s", payload)
    except struct.error:
        return None

    if plan_byte not in _PLAN_REVERSE:
        return None

    plan = _PLAN_REVERSE[plan_byte]
    dev_prefix_hex = dev_prefix.hex()

    # Verify signature
    full_data = payload + bytes.fromhex(current_device_hash)
    if not _hmac_verify_compact(full_data, sig):
        return {"ok": False, "error": "啟用碼簽章不正確"}

    # Verify device binding
    if current_device_hash[:8] != dev_prefix_hex:
        return {"ok": False, "error": "此啟用碼不屬於本裝置"}

    # Check expiration
    expires_date = _EPOCH + timedelta(days=epoch_day)
    expires = datetime(expires_date.year, expires_date.month, expires_date.day,
                       hour=23, minute=59, second=59, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return {"ok": False, "error": "啟用碼已過期"}

    return {
        "ok": True,
        "payload": {
            "device_id_hash": current_device_hash,
            "expires_at": expires.isoformat(),
            "plan": plan,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ── Public API ──

def issue_license_code(device_hash_or_display: str, days: int, plan: str) -> str:
    """
    Generate a compact activation code for a device.
    Accepts either a full device hash (64 hex) or a display code.
    On same machine, display code resolves to local hash.
    """
    display_clean = device_hash_or_display.upper().replace("BG-", "").replace("-", "")

    if len(display_clean) <= 16:
        # Display code — resolve locally
        device_hash = _device_id_hash()
        if device_hash[:12].upper() != display_clean:
            raise ValueError(f"設備碼 {device_hash_or_display} 不屬於本機")
    else:
        device_hash = display_clean.lower()

    return _issue_compact(device_hash, days, plan)


def verify_activation_code(code: str, device_id_display: str | None = None) -> dict:
    """
    Verify an activation code (v2 compact or v1 legacy hex).
    Returns {"ok": False, "error": "..."} or {"ok": True, "payload": {...}}.
    """
    code = code.strip()
    current_hash = _device_id_hash()

    # ── Try v2 compact format ──
    cleaned = code.upper()
    # Strip prefix (BG7- or BG30-)
    if cleaned.startswith("BG") and "-" in cleaned[:6]:
        cleaned = cleaned[cleaned.index("-") + 1:]
    cleaned_compact = cleaned.replace("-", "")
    try:
        raw = _b32_decode(cleaned_compact)
        result = _verify_compact(raw, current_hash)
        if result is not None:
            return result
    except Exception:
        pass

    # ── Try v1 legacy hex format ──
    try:
        raw = bytes.fromhex(cleaned.replace("-", ""))
        payload_str = raw[:-32].decode("utf-8")
        provided_sig = raw[-32:].hex()
        payload = json.loads(payload_str)
    except Exception:
        return {"ok": False, "error": "啟用碼格式無效"}

    if not _verify_signature(payload, provided_sig):
        return {"ok": False, "error": "啟用碼簽章不正確"}

    device_hash = payload.get("device_id_hash", "")
    if device_id_display and _device_id_matches_display(device_hash, device_id_display):
        pass
    elif device_hash != current_hash:
        return {"ok": False, "error": "此啟用碼不屬於本裝置"}

    try:
        expires = datetime.fromisoformat(payload["expires_at"])
    except Exception:
        return {"ok": False, "error": "啟用碼日期格式錯誤"}

    if datetime.now(timezone.utc) > expires.astimezone(timezone.utc):
        return {"ok": False, "error": "啟用碼已過期"}

    return {"ok": True, "payload": payload}


def _device_id_matches_display(device_hash: str, display: str) -> bool:
    expected = device_hash[:16].upper()
    display_clean = display.upper().replace("BG-", "").replace("-", "")
    return display_clean == expected


def load_license() -> dict | None:
    try:
        if not os.path.exists(_LICENSE_FILE):
            return None
        with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not _verify_signature(
            {"device_id_hash": data["device_id_hash"],
             "expires_at": data["expires_at"],
             "plan": data["plan"],
             "issued_at": data["issued_at"]},
            data["signature"],
        ):
            return None
        return data
    except Exception:
        return None


def save_license(payload: dict) -> None:
    os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
    signature = _sign(payload)
    data = {**payload, "signature": signature}
    with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_license_active() -> bool:
    lic = load_license()
    if not lic:
        return False
    try:
        expires = datetime.fromisoformat(lic["expires_at"])
        return datetime.now(timezone.utc) < expires.astimezone(timezone.utc)
    except Exception:
        return False


def license_status() -> dict:
    lic = load_license()
    if not lic:
        return {"status": "inactive", "device_id": get_device_id()}
    try:
        expires = datetime.fromisoformat(lic["expires_at"])
        active = datetime.now(timezone.utc) < expires.astimezone(timezone.utc)
        return {
            "status": "active" if active else "expired",
            "expires_at": lic["expires_at"],
            "plan": lic.get("plan", "unknown"),
            "device_id": get_device_id(),
            "issued_at": lic.get("issued_at", ""),
        }
    except Exception:
        return {"status": "expired", "device_id": get_device_id()}


# ── CLI ──

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m betguard.license",
        description="Betguard license activation code issuer",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue_cmd = sub.add_parser("issue", help="Generate an activation code")
    issue_cmd.add_argument("--device", required=True,
                           help="Device code (e.g. BG-760D-B3DD-4639) or full device hash (64 hex chars)")
    issue_cmd.add_argument("--days", type=int, required=True,
                           help="Number of days until expiration (e.g. 7, 30)")
    issue_cmd.add_argument("--plan", required=True,
                           choices=["trial_7d", "trial_30d"],
                           help="License plan")

    args = parser.parse_args()

    if args.command == "issue":
        device_input = args.device.strip()
        display_clean = device_input.upper().replace("BG-", "").replace("-", "")

        device_hash: str
        if len(display_clean) <= 16:
            device_hash = _device_id_hash()
            if device_hash[:12].upper() != display_clean:
                print(f"錯誤：設備碼 {device_input} 不屬於本機", file=sys.stderr)
                print(f"本機設備碼：{get_device_id()}", file=sys.stderr)
                print(f"本機完整 hash：{device_hash}", file=sys.stderr)
                sys.exit(1)
        else:
            device_hash = display_clean.lower()

        try:
            code = issue_license_code(device_hash, args.days, args.plan)
            print(code)
        except Exception as e:
            print(f"錯誤：{e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
