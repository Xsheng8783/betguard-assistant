"""
License system — device-bound Ed25519-signed activation codes.

Production mode: only BG7E / BG30E (Ed25519) codes accepted.
Legacy HMAC codes only available when BETGUARD_ALLOW_LEGACY_LICENSES=1 (testing only).

Activation code format (v3):
  payload(13): license_id(4) | plan(1) | issued_day(2) | expires_day(2) | dev_prefix(4)
  + Ed25519 signature (64 bytes) = 77 bytes, base32 encoded

Storage (license.json):
  - license_id, activation_code_hash, activated_at
  - signed_payload_b64, ed25519_signature_b64 (verified on every load)
  - plan/expires_at derived from verified payload, never trusted from disk

Client EXE contains only Ed25519 public key; private key is admin-only.
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
_LICENSE_PUBLIC_KEY_B64 = os.environ.get(
    "BETGUARD_LICENSE_PUBLIC_KEY",
    "lknAGLQUvCb3y23zRPVaw7TeL9XRdiCaj4oADSjuzD0=",
)

_LICENSE_FILE = os.path.join(get_data_dir(), "license.json")
_EPOCH = date(2024, 1, 1)

PLANS = {"trial_7d": 7, "trial_30d": 30}
_PLAN_BYTES = {"trial_7d": 0x07, "trial_30d": 0x1E}
_PLAN_REVERSE = {0x07: "trial_7d", 0x1E: "trial_30d"}

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_B32_DECODE = {c: i for i, c in enumerate(_B32)}

_ALLOW_LEGACY = os.environ.get("BETGUARD_ALLOW_LEGACY_LICENSES") == "1"


def _get_public_key_bytes() -> bytes:
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


def _ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except ImportError:
        try:
            import nacl.bindings
            return nacl.bindings.crypto_sign_verify_detached(signature, message, public_key)
        except ImportError:
            raise ImportError("需要 cryptography 或 pynacl 套件以驗證 Ed25519 簽章")


def _machine_id() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
            return winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        pass
    import platform
    return hashlib.sha256((platform.node() + str(uuid.getnode())).encode()).hexdigest()


def get_device_id() -> str:
    h = hashlib.sha256(_machine_id().encode()).hexdigest().upper()
    return f"BG-{h[:4]}-{h[4:8]}-{h[8:12]}"


def _device_id_hash() -> str:
    return hashlib.sha256(_machine_id().encode()).hexdigest()


def _normalize_code(code: str) -> str:
    return code.strip().upper().replace("-", "")


def _activation_code_hash(code: str) -> str:
    return hashlib.sha256(_normalize_code(code).encode()).hexdigest()


# ── License request code (client → admin) ──

def get_request_code() -> str:
    device_hash = _device_id_hash()
    ts = int(datetime.now(timezone.utc).timestamp()) & 0xFFFFFFFF
    payload = bytes.fromhex(device_hash) + struct.pack(">I", ts)
    encoded = _b32_encode(payload)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    return "BRQ-" + "-".join(chunks)


# ── Legacy HMAC helpers (only when BETGUARD_ALLOW_LEGACY_LICENSES=1) ──

def _legacy_secret() -> str:
    secret = os.environ.get("BETGUARD_LICENSE_SECRET", "")
    if not secret:
        if _ALLOW_LEGACY:
            secret = "betguard-local-license-secret-v1"  # dev default
        else:
            return ""
    return secret


def _legacy_issue_compact(device_hash: str, days: int, plan: str) -> str:
    secret = _legacy_secret()
    if not secret:
        raise RuntimeError("Legacy HMAC codes disabled — use Ed25519 via license_issuer")
    plan_byte = _PLAN_BYTES[plan]
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    epoch_day = (expires.date() - _EPOCH).days
    dev_prefix = bytes.fromhex(device_hash)[:4]
    payload = struct.pack(">BH4s", plan_byte, epoch_day, dev_prefix)
    sig = hmac.new(secret.encode(), payload + bytes.fromhex(device_hash), hashlib.sha256).digest()[:6]
    encoded = _b32_encode(payload + sig)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    return ("BG7-" if plan == "trial_7d" else "BG30-") + "-".join(chunks)


def _legacy_verify(code: str, current_hash: str) -> dict | None:
    if not _ALLOW_LEGACY:
        return None
    try:
        cleaned = code.upper()
        if cleaned.startswith("BG") and "-" in cleaned[:6]:
            cleaned = cleaned[cleaned.index("-") + 1:]
        cleaned = cleaned.replace("-", "")

        # Try v2 compact HMAC
        secret = _legacy_secret()
        if secret:
            raw = _b32_decode(cleaned)
            if len(raw) == 13:
                payload, sig = raw[:7], raw[7:]
                try:
                    plan_byte, epoch_day, dev_prefix = struct.unpack(">BH4s", payload)
                except struct.error:
                    return None
                if plan_byte in _PLAN_REVERSE:
                    expected_sig = hmac.new(secret.encode(), payload + bytes.fromhex(current_hash),
                                            hashlib.sha256).digest()[:6]
                    if hmac.compare_digest(expected_sig, sig):
                        if current_hash[:8] != dev_prefix.hex():
                            return {"ok": False, "error": "此啟用碼不屬於本裝置"}
                        plan = _PLAN_REVERSE[plan_byte]
                        expires_date = _EPOCH + timedelta(days=epoch_day)
                        expires = datetime(expires_date.year, expires_date.month, expires_date.day,
                                           hour=23, minute=59, second=59, tzinfo=timezone.utc)
                        if datetime.now(timezone.utc) > expires:
                            return {"ok": False, "error": "啟用碼已過期"}
                        return {"ok": True, "payload": {
                            "device_id_hash": current_hash,
                            "expires_at": expires.isoformat(),
                            "plan": plan,
                            "issued_at": datetime.now(timezone.utc).isoformat(),
                        }}

        # Try v1 hex
        raw = bytes.fromhex(cleaned)
        payload_str = raw[:-32].decode("utf-8")
        provided_sig = raw[-32:].hex()
        payload = json.loads(payload_str)
        expected = hmac.new(secret.encode(),
                            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(),
                            hashlib.sha256).hexdigest() if secret else ""
        if secret and hmac.compare_digest(expected, provided_sig):
            if payload.get("device_id_hash") != current_hash:
                return {"ok": False, "error": "此啟用碼不屬於本裝置"}
            expires = datetime.fromisoformat(payload["expires_at"])
            if datetime.now(timezone.utc) > expires.astimezone(timezone.utc):
                return {"ok": False, "error": "啟用碼已過期"}
            return {"ok": True, "payload": payload}
    except Exception:
        pass
    return None


# ── Ed25519 activation codes ──

def _issue_ed25519(device_hash: str, days: int, plan: str, license_id: bytes | None = None) -> str:
    if license_id is None:
        license_id = os.urandom(4)
    plan_byte = _PLAN_BYTES[plan]
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(days=days)
    issued_day = (issued.date() - _EPOCH).days
    expires_day = (expires.date() - _EPOCH).days
    dev_prefix = bytes.fromhex(device_hash)[:4]
    payload = struct.pack(">4sBHH4s", license_id, plan_byte, issued_day, expires_day, dev_prefix)

    private_key_b64 = os.environ.get("BETGUARD_LICENSE_PRIVATE_KEY", "")
    if not private_key_b64:
        raise ValueError("BETGUARD_LICENSE_PRIVATE_KEY not set")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
        signature = key.sign(payload)
    except ImportError:
        import nacl.bindings
        pk = base64.b64decode(private_key_b64)
        signature = nacl.bindings.crypto_sign_detached(payload, pk + _get_public_key_bytes())

    encoded = _b32_encode(payload + signature)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    prefix_map = {"trial_7d": "BG7E-", "trial_30d": "BG30E-"}
    return prefix_map.get(plan, "BGXE-") + "-".join(chunks)


def _verify_ed25519(raw: bytes, current_device_hash: str) -> dict | None:
    if len(raw) != 77:
        return None
    payload, signature = raw[:13], raw[13:]
    try:
        license_id, plan_byte, issued_day, expires_day, dev_prefix = \
            struct.unpack(">4sBHH4s", payload)
    except struct.error:
        return None
    if plan_byte not in _PLAN_REVERSE:
        return None
    plan = _PLAN_REVERSE[plan_byte]
    license_id_hex = license_id.hex().upper()

    try:
        if not _ed25519_verify(_get_public_key_bytes(), payload, signature):
            return {"ok": False, "error": "啟用碼簽章不正確"}
    except ImportError as e:
        return {"ok": False, "error": f"無法驗證簽章：{e}"}

    if current_device_hash[:8] != dev_prefix.hex():
        return {"ok": False, "error": "此啟用碼不屬於本裝置"}

    issued = datetime.combine(_EPOCH + timedelta(days=issued_day), datetime.min.time(),
                              tzinfo=timezone.utc)
    expires = datetime.combine(_EPOCH + timedelta(days=expires_day), datetime.max.time(),
                               tzinfo=timezone.utc) - timedelta(microseconds=1)

    if datetime.now(timezone.utc) > expires:
        return {"ok": False, "error": "啟用碼已過期", "expired": True, "license_id": license_id_hex}

    return {
        "ok": True,
        "license_id": license_id_hex,
        "device_id_hash": current_device_hash,
        "expires_at": expires.isoformat(),
        "plan": plan,
        "issued_at": issued.isoformat(),
        "signed_payload_b64": base64.b64encode(payload).decode(),
        "ed25519_signature_b64": base64.b64encode(signature).decode(),
    }


# ── Secure license storage (Ed25519 verified) ──

def _load_license_json() -> dict | None:
    try:
        if not os.path.exists(_LICENSE_FILE):
            return None
        with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_license_secure(storage: dict) -> None:
    os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
    out = dict(storage)
    out.pop("activation_code", None)
    with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def _save_license_data(data: dict) -> None:
    """Legacy HMAC-signed save (testing only)."""
    os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
    secret = _legacy_secret()
    sig_payload = {
        "license_id": data.get("license_id", ""),
        "device_id_hash": data["device_id_hash"],
        "expires_at": data["expires_at"],
        "plan": data["plan"],
        "issued_at": data["issued_at"],
    }
    raw = json.dumps(sig_payload, sort_keys=True, ensure_ascii=False)
    signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest() if secret else ""
    out = {**data, "signature": signature}
    out.pop("activation_code", None)
    with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


# ── Public API ──

def issue_license_code(device_hash_or_display: str, days: int, plan: str) -> str:
    """
    Legacy HMAC code issuer. Only works when BETGUARD_ALLOW_LEGACY_LICENSES=1.
    In production, raises RuntimeError.
    """
    if not _ALLOW_LEGACY:
        raise RuntimeError("Legacy HMAC codes disabled in production — use betguard.license_issuer")
    display_clean = device_hash_or_display.upper().replace("BG-", "").replace("-", "")
    if len(display_clean) <= 16:
        device_hash = _device_id_hash()
        if device_hash[:12].upper() != display_clean:
            raise ValueError(f"設備碼 {device_hash_or_display} 不屬於本機")
    else:
        device_hash = display_clean.lower()
    return _legacy_issue_compact(device_hash, days, plan)


def verify_activation_code(code: str, device_id_display: str | None = None) -> dict:
    """Verify activation code. Production: BG7E/BG30E only."""
    code = code.strip()
    current_hash = _device_id_hash()
    is_ed25519 = code.upper().startswith("BG7E-") or code.upper().startswith("BG30E-")
    cleaned = code.upper()
    if cleaned.startswith("BG") and "-" in cleaned[:6]:
        cleaned = cleaned[cleaned.index("-") + 1:]
    cleaned = cleaned.replace("-", "")

    # Ed25519 (always accepted)
    try:
        if is_ed25519 or len(code) > 50:
            raw = _b32_decode(cleaned)
            r = _verify_ed25519(raw, current_hash)
            if r is not None:
                return r
    except Exception:
        if is_ed25519:
            return {"ok": False, "error": "啟用碼格式無效"}

    # Legacy (only when flag is set)
    if _ALLOW_LEGACY:
        r = _legacy_verify(code, current_hash)
        if r is not None:
            return r
    elif not is_ed25519:
        return {"ok": False, "error": "此授權格式已不支援，請使用 BG7E 或 BG30E 啟用碼"}

    return {"ok": False, "error": "啟用碼格式無效"}


def load_license() -> dict | None:
    """Load and verify license from disk via Ed25519 (or legacy HMAC if allowed)."""
    data = _load_license_json()
    if not data:
        return None

    # Ed25519 path (production)
    payload_b64 = data.get("signed_payload_b64", "")
    sig_b64 = data.get("ed25519_signature_b64", "")
    if payload_b64 and sig_b64:
        try:
            payload = base64.b64decode(payload_b64)
            signature = base64.b64decode(sig_b64)
            if not _ed25519_verify(_get_public_key_bytes(), payload, signature):
                return None
            license_id_bytes, plan_byte, issued_day, expires_day, dev_prefix = \
                struct.unpack(">4sBHH4s", payload)
            if plan_byte not in _PLAN_REVERSE:
                return None
            if _device_id_hash()[:8] != dev_prefix.hex():
                return None
            plan = _PLAN_REVERSE[plan_byte]
            issued = datetime.combine(_EPOCH + timedelta(days=issued_day), datetime.min.time(),
                                      tzinfo=timezone.utc)
            expires = datetime.combine(_EPOCH + timedelta(days=expires_day), datetime.max.time(),
                                       tzinfo=timezone.utc) - timedelta(microseconds=1)
            return {
                "license_id": license_id_bytes.hex().upper(),
                "device_id_hash": _device_id_hash(),
                "plan": plan,
                "issued_at": issued.isoformat(),
                "expires_at": expires.isoformat(),
                "activated_at": data.get("activated_at", ""),
                "activation_code_hash": data.get("activation_code_hash", ""),
            }
        except Exception:
            return None

    # Legacy HMAC path (testing only)
    if _ALLOW_LEGACY:
        secret = _legacy_secret()
        if not secret:
            return None
        try:
            sig_payload = {
                "license_id": data.get("license_id", ""),
                "device_id_hash": data["device_id_hash"],
                "expires_at": data["expires_at"],
                "plan": data["plan"],
                "issued_at": data["issued_at"],
            }
            raw = json.dumps(sig_payload, sort_keys=True, ensure_ascii=False)
            expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, data.get("signature", "")):
                return None
            return data
        except Exception:
            return None

    return None


def save_license(payload: dict) -> None:
    """Save license data. Uses Ed25519 storage in production, legacy HMAC in test mode."""
    if _ALLOW_LEGACY and not payload.get("signed_payload_b64"):
        # Legacy HMAC storage
        os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
        sig_payload = {
            "license_id": payload.get("license_id", ""),
            "device_id_hash": payload["device_id_hash"],
            "expires_at": payload["expires_at"],
            "plan": payload["plan"],
            "issued_at": payload["issued_at"],
        }
        raw = json.dumps(sig_payload, sort_keys=True, ensure_ascii=False)
        secret = _legacy_secret()
        signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest() if secret else ""
        out = {**payload, "signature": signature}
        out.pop("activation_code", None)
        with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    else:
        _save_license_secure(payload)


def is_license_active() -> bool:
    lic = load_license()
    if not lic:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(lic["expires_at"]).astimezone(timezone.utc)
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


def activate_license(code: str) -> dict:
    """Activate a license code with duplicate detection and renewal logic."""
    code = code.strip()
    result = verify_activation_code(code)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "啟用碼無效"),
                "activated": False, "already_active": False, "updated": False}

    license_id = result.get("license_id", "")
    plan = result.get("plan", "")
    issued_at = result.get("issued_at", "")
    expires_at = result.get("expires_at", "")
    signed_b64 = result.get("signed_payload_b64", "")
    sig_b64 = result.get("ed25519_signature_b64", "")

    # For v2/v1 legacy, fields are wrapped in "payload"
    if "payload" in result:
        p = result["payload"]
        if not license_id:
            license_id = p.get("license_id", "")
        if not plan:
            plan = p.get("plan", "")
        if not issued_at:
            issued_at = p.get("issued_at", "")
        if not expires_at:
            expires_at = p.get("expires_at", "")
    code_hash = _activation_code_hash(code)
    activated_at = datetime.now(timezone.utc).isoformat()

    current = load_license()

    # Duplicate detection
    if license_id and current and current.get("license_id") == license_id:
        return _already_active(current)
    if current and current.get("activation_code_hash") == code_hash:
        return _already_active(current)

    # Renewal check
    if current:
        try:
            cur_exp = datetime.fromisoformat(current["expires_at"])
            new_exp = datetime.fromisoformat(expires_at)
        except Exception:
            cur_exp = new_exp = None
        if cur_exp and new_exp and new_exp <= cur_exp:
            reason = ("目前已有期限更長的授權" if new_exp < cur_exp else "目前已有相同期限的授權")
            return {**current, "ok": True, "activated": False, "already_active": True,
                    "updated": False, "renewed": False, "message": reason,
                    "previous_expires_at": current.get("expires_at", "")}

    storage = {
        "license_id": license_id,
        "activation_code_hash": code_hash,
        "activated_at": activated_at,
    }
    if signed_b64:
        storage["signed_payload_b64"] = signed_b64
    if sig_b64:
        storage["ed25519_signature_b64"] = sig_b64

    if _ALLOW_LEGACY and not signed_b64:
        # Legacy HMAC storage
        storage.update({
            "device_id_hash": _device_id_hash(),
            "plan": plan,
            "issued_at": issued_at,
            "expires_at": expires_at,
        })
        _save_license_data(storage)
    else:
        _save_license_secure(storage)

    return {"ok": True, "activated": True, "already_active": False, "updated": True,
            "renewed": current is not None,
            "previous_expires_at": current.get("expires_at", "") if current else "",
            "plan": plan, "issued_at": issued_at, "activated_at": activated_at,
            "expires_at": expires_at, "license_id": license_id}


def _already_active(current: dict) -> dict:
    return {"ok": True, "activated": False, "already_active": True, "updated": False,
            "renewed": False, "plan": current.get("plan", ""),
            "issued_at": current.get("issued_at", ""),
            "activated_at": current.get("activated_at", ""),
            "expires_at": current.get("expires_at", ""),
            "license_id": current.get("license_id", "")}


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="python -m betguard.license")
    sub = parser.add_subparsers(dest="command", required=True)
    issue_cmd = sub.add_parser("issue", help="Generate legacy HMAC code (testing only)")
    issue_cmd.add_argument("--device", required=True)
    issue_cmd.add_argument("--days", type=int, required=True)
    issue_cmd.add_argument("--plan", required=True, choices=["trial_7d", "trial_30d"])
    sub.add_parser("request-code", help="Generate license request code for admin")

    args = parser.parse_args()
    if args.command == "issue":
        device_input = args.device.strip()
        display_clean = device_input.upper().replace("BG-", "").replace("-", "")
        device_hash: str
        if len(display_clean) <= 16:
            device_hash = _device_id_hash()
            if device_hash[:12].upper() != display_clean:
                print(f"錯誤：設備碼 {device_input} 不屬於本機", file=sys.stderr)
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


# ── Test-only exports (available when BETGUARD_ALLOW_LEGACY_LICENSES=1) ──

def _sign(payload: dict) -> str:
    """HMAC sign for test verification only."""
    secret = _legacy_secret()
    if not secret:
        raise RuntimeError("Legacy disabled")
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _verify_signature(payload: dict, signature: str) -> bool:
    return hmac.compare_digest(_sign(payload), signature)


def _issue_compact(device_hash: str, days: int, plan: str) -> str:
    return _legacy_issue_compact(device_hash, days, plan)


def _hmac_sign_compact(data: bytes) -> bytes:
    """HMAC sign for test-only expired code generation."""
    secret = _legacy_secret()
    if not secret:
        raise RuntimeError("Legacy disabled")
    return hmac.new(secret.encode(), data, hashlib.sha256).digest()[:6]
