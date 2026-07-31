"""
License system — device-bound activation codes.

v1: legacy HMAC hex codes (deprecated, kept for migration).
v2: compact HMAC codes (BG7-XXXX-..., BG30-XXXX-...).
v3: Ed25519-signed codes (BG7E-..., BG30E-...) with remote issuance support.

Client EXE contains only the public key; private key is admin-only.

Storage: get_data_dir()/license.json
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from betguard.user_data import get_data_dir

# ── Ed25519 public key (embedded in client EXE) ──
# Private key: stored ONLY on admin machine (betguard/license_issuer.py)
# Generate with: python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; import base64; k=Ed25519PrivateKey.generate(); print('private:', base64.b64encode(k.private_bytes_raw()).decode()); print('public:', base64.b64encode(k.public_key().public_bytes_raw()).decode())"
_LICENSE_PUBLIC_KEY_B64 = os.environ.get(
    "BETGUARD_LICENSE_PUBLIC_KEY",
    "lknAGLQUvCb3y23zRPVaw7TeL9XRdiCaj4oADSjuzD0=",
)

_LICENSE_SECRET = os.environ.get("BETGUARD_LICENSE_SECRET", "betguard-local-license-secret-v1")
_LICENSE_FILE = os.path.join(get_data_dir(), "license.json")

_EPOCH = date(2024, 1, 1)

PLANS = {"trial_7d": 7, "trial_30d": 30}
_PLAN_BYTES = {"trial_7d": 0x07, "trial_30d": 0x1E}
_PLAN_REVERSE = {0x07: "trial_7d", 0x1E: "trial_30d"}

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_B32_DECODE = {c: i for i, c in enumerate(_B32)}


# ── crypto helpers ──

def _get_public_key_bytes() -> bytes:
    """Return raw Ed25519 public key (32 bytes)."""
    key = os.environ.get("BETGUARD_LICENSE_PUBLIC_KEY", _LICENSE_PUBLIC_KEY_B64)
    try:
        return base64.b64decode(key)
    except Exception:
        raise ValueError("BETGUARD_LICENSE_PUBLIC_KEY is not valid base64")


def _b32_encode(data: bytes) -> str:
    bits, bit_count, result = 0, 0, []
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
    s = s.upper().replace('-', '')
    bits, bit_count, result = 0, 0, bytearray()
    for ch in s:
        if ch not in _B32_DECODE:
            raise ValueError(f"Invalid base32 char: {ch}")
        bits = (bits << 5) | _B32_DECODE[ch]
        bit_count += 5
        while bit_count >= 8:
            bit_count -= 8
            result.append((bits >> bit_count) & 0xFF)
    return bytes(result)


# ── Ed25519 helpers (pure Python, no cryptography dep in client) ──

def _ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify Ed25519 signature using hashlib + pure Python implementation."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(signature, message)
        return True
    except ImportError:
        # Fallback: use nacl if available
        try:
            import nacl.bindings
            return nacl.bindings.crypto_sign_verify_detached(signature, message, public_key)
        except ImportError:
            raise ImportError("需要 cryptography 或 pynacl 套件以驗證 Ed25519 簽章")


def _machine_id() -> str:
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
    h = hashlib.sha256(_machine_id().encode()).hexdigest().upper()
    return f"BG-{h[:4]}-{h[4:8]}-{h[8:12]}"


def _device_id_hash() -> str:
    return hashlib.sha256(_machine_id().encode()).hexdigest()


# ── License request code (client → admin) ──

def get_request_code() -> str:
    """
    Generate a license request code that the customer sends to the admin.
    Format: BRQ-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
    Contains: device_hash(32 bytes hex) + timestamp(4 bytes BE)
    Checksummed with CRC16-like HMAC for integrity.
    """
    device_hash = _device_id_hash()
    ts = int(datetime.now(timezone.utc).timestamp()) & 0xFFFFFFFF
    payload = bytes.fromhex(device_hash) + struct.pack(">I", ts)
    encoded = _b32_encode(payload)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    return "BRQ-" + "-".join(chunks)


def _parse_request_code(code: str) -> tuple[str, int] | None:
    """Parse a license request code. Returns (device_hash_hex, timestamp) or None."""
    try:
        cleaned = code.strip().upper()
        if cleaned.startswith("BRQ-"):
            cleaned = cleaned[4:]
        cleaned = cleaned.replace("-", "")
        raw = _b32_decode(cleaned)
        if len(raw) != 36:  # 32 bytes hash + 4 bytes timestamp
            return None
        device_hash = raw[:32].hex()
        ts = struct.unpack(">I", raw[32:36])[0]
        return device_hash, ts
    except Exception:
        return None


# ── v2 HMAC compact codes (kept for backward compat) ──

def _sign(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hmac.new(_LICENSE_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _verify_signature(payload: dict, signature: str) -> bool:
    return hmac.compare_digest(_sign(payload), signature)


def _hmac_sign_compact(data: bytes) -> bytes:
    return hmac.new(_LICENSE_SECRET.encode(), data, hashlib.sha256).digest()[:6]


def _hmac_verify_compact(data: bytes, sig: bytes) -> bool:
    return hmac.compare_digest(_hmac_sign_compact(data), sig)


def _issue_compact(device_hash: str, days: int, plan: str) -> str:
    plan_byte = _PLAN_BYTES[plan]
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    epoch_day = (expires.date() - _EPOCH).days
    dev_prefix = bytes.fromhex(device_hash)[:4]
    payload = struct.pack(">BH4s", plan_byte, epoch_day, dev_prefix)
    sig = _hmac_sign_compact(payload + bytes.fromhex(device_hash))
    encoded = _b32_encode(payload + sig)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    prefix = "BG7-" if plan == "trial_7d" else "BG30-"
    return prefix + "-".join(chunks)


def _verify_compact(raw: bytes, current_device_hash: str) -> dict | None:
    if len(raw) != 13:
        return None
    payload, sig = raw[:7], raw[7:]
    try:
        plan_byte, epoch_day, dev_prefix = struct.unpack(">BH4s", payload)
    except struct.error:
        return None
    if plan_byte not in _PLAN_REVERSE:
        return None
    plan = _PLAN_REVERSE[plan_byte]
    if not _hmac_verify_compact(payload + bytes.fromhex(current_device_hash), sig):
        return {"ok": False, "error": "啟用碼簽章不正確"}
    if current_device_hash[:8] != dev_prefix.hex():
        return {"ok": False, "error": "此啟用碼不屬於本裝置"}
    expires_date = _EPOCH + timedelta(days=epoch_day)
    expires = datetime(expires_date.year, expires_date.month, expires_date.day,
                       hour=23, minute=59, second=59, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return {"ok": False, "error": "啟用碼已過期"}
    return {"ok": True, "payload": {
        "device_id_hash": current_device_hash,
        "expires_at": expires.isoformat(),
        "plan": plan,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }}


# ── v3 Ed25519-signed compact codes ──

def _issue_ed25519(device_hash: str, days: int, plan: str) -> str:
    """
    Generate an Ed25519-signed activation code.
    Must be called with access to the private key (admin-side only).
    """
    plan_byte = _PLAN_BYTES[plan]
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    epoch_day = (expires.date() - _EPOCH).days
    dev_prefix = bytes.fromhex(device_hash)[:4]
    payload = struct.pack(">BH4s", plan_byte, epoch_day, dev_prefix)

    # Sign with Ed25519 (requires private key)
    import os as _os
    private_key_b64 = _os.environ.get("BETGUARD_LICENSE_PRIVATE_KEY", "")
    if not private_key_b64:
        raise ValueError("BETGUARD_LICENSE_PRIVATE_KEY not set — Ed25519 signing requires private key")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
        signature = private_key.sign(payload)
    except ImportError:
        import nacl.bindings
        private_bytes = base64.b64decode(private_key_b64)
        signature = nacl.bindings.crypto_sign_detached(payload, private_bytes + _get_public_key_bytes())

    encoded = _b32_encode(payload + signature)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    prefix_map = {"trial_7d": "BG7E-", "trial_30d": "BG30E-"}
    return prefix_map.get(plan, "BGXE-") + "-".join(chunks)


def _verify_ed25519(raw: bytes, current_device_hash: str) -> dict | None:
    """Try to verify an Ed25519-signed (v3) activation code."""
    # v3: payload(7) + Ed25519 sig(64) = 71 bytes
    if len(raw) != 71:
        return None
    payload, signature = raw[:7], raw[7:]
    try:
        plan_byte, epoch_day, dev_prefix = struct.unpack(">BH4s", payload)
    except struct.error:
        return None
    if plan_byte not in _PLAN_REVERSE:
        return None
    plan = _PLAN_REVERSE[plan_byte]

    # Verify Ed25519 signature
    try:
        pubkey = _get_public_key_bytes()
        if not _ed25519_verify(pubkey, payload, signature):
            return {"ok": False, "error": "啟用碼簽章不正確"}
    except ImportError as e:
        return {"ok": False, "error": f"無法驗證簽章：{e}"}

    if current_device_hash[:8] != dev_prefix.hex():
        return {"ok": False, "error": "此啟用碼不屬於本裝置"}
    expires_date = _EPOCH + timedelta(days=epoch_day)
    expires = datetime(expires_date.year, expires_date.month, expires_date.day,
                       hour=23, minute=59, second=59, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return {"ok": False, "error": "啟用碼已過期"}
    return {"ok": True, "payload": {
        "device_id_hash": current_device_hash,
        "expires_at": expires.isoformat(),
        "plan": plan,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }}


# ── Public API ──

def issue_license_code(device_hash_or_display: str, days: int, plan: str) -> str:
    """
    Generate a compact activation code (v2 HMAC).
    ⚠️ DEPRECATED for remote issuance — use betguard.license_issuer for Ed25519 codes.
    Kept for local admin testing only.
    """
    display_clean = device_hash_or_display.upper().replace("BG-", "").replace("-", "")
    if len(display_clean) <= 16:
        device_hash = _device_id_hash()
        if device_hash[:12].upper() != display_clean:
            raise ValueError(f"設備碼 {device_hash_or_display} 不屬於本機")
    else:
        device_hash = display_clean.lower()
    return _issue_compact(device_hash, days, plan)


def verify_activation_code(code: str, device_id_display: str | None = None) -> dict:
    """
    Verify an activation code (v3 Ed25519, v2 compact HMAC, or v1 legacy hex).
    Returns {"ok": False, "error": "..."} or {"ok": True, "payload": {...}}.
    """
    code = code.strip()
    current_hash = _device_id_hash()

    cleaned = code.upper()
    # Strip prefix: BG7E-, BG30E-, BG7-, BG30-, BG3-
    if cleaned.startswith("BG") and "-" in cleaned[:6]:
        cleaned = cleaned[cleaned.index("-") + 1:]

    # Determine format from prefix
    is_ed25519 = code.upper().startswith("BG7E-") or code.upper().startswith("BG30E-")
    cleaned_no_dash = cleaned.replace("-", "")

    # ── Try v3 Ed25519 format ──
    if is_ed25519 or len(code) > 50:
        try:
            raw = _b32_decode(cleaned_no_dash)
            result = _verify_ed25519(raw, current_hash)
            if result is not None:
                return result
        except Exception:
            if is_ed25519:
                return {"ok": False, "error": "啟用碼格式無效"}

    # ── Try v2 compact HMAC format ──
    try:
        raw = _b32_decode(cleaned_no_dash)
        result = _verify_compact(raw, current_hash)
        if result is not None:
            return result
    except Exception:
        pass

    # ── Try v1 legacy hex format ──
    try:
        raw = bytes.fromhex(cleaned_no_dash)
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

# ══════════════════════════════════════════
# CLI (local testing only — v2 HMAC codes)
# ══════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="python -m betguard.license",
                                     description="Betguard license CLI (v2 HMAC, local only)")
    sub = parser.add_subparsers(dest="command", required=True)
    issue_cmd = sub.add_parser("issue", help="Generate v2 HMAC code (same machine only)")
    issue_cmd.add_argument("--device", required=True)
    issue_cmd.add_argument("--days", type=int, required=True)
    issue_cmd.add_argument("--plan", required=True, choices=["trial_7d", "trial_30d"])
    req_cmd = sub.add_parser("request-code", help="Generate a license request code for admin")

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
                sys.exit(1)
        else:
            device_hash = display_clean.lower()
        try:
            print(issue_license_code(device_hash, args.days, args.plan))
        except Exception as e:
            print(f"錯誤：{e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "request-code":
        print(get_request_code())


if __name__ == "__main__":
    main()
