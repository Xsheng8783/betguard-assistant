"""
Admin-side license issuer — uses Ed25519 private key to sign activation codes.

This file must NOT be bundled in client EXE.
Private key is loaded from BETGUARD_LICENSE_PRIVATE_KEY env var or ~/.betguard/license_key.

Usage:
  python -m betguard.license_issuer issue --request-code "<BRQ-...>" --days 7 --plan trial_7d
  python -m betguard.license_issuer issue --request-code "<BRQ-...>" --days 30 --plan trial_30d
  python -m betguard.license_issuer generate-key    (generate a new keypair)
"""

import base64
import os
import struct
import sys
from datetime import date, datetime, timedelta, timezone

# ── Key management ──

_PRIVATE_KEY_PATH = os.path.join(os.path.expanduser("~"), ".betguard", "license_key")


def _load_private_key() -> bytes:
    """Load Ed25519 private key (32 bytes raw) from env or file."""
    key_b64 = os.environ.get("BETGUARD_LICENSE_PRIVATE_KEY", "")
    if key_b64:
        return base64.b64decode(key_b64)
    if os.path.exists(_PRIVATE_KEY_PATH):
        with open(_PRIVATE_KEY_PATH, "r") as f:
            return base64.b64decode(f.read().strip())
    raise FileNotFoundError(
        f"找不到授權私鑰。請設定 BETGUARD_LICENSE_PRIVATE_KEY 環境變數或建立 {_PRIVATE_KEY_PATH}"
    )


def generate_keypair():
    """Generate a new Ed25519 keypair and print instructions."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes_raw()
        public_bytes = private_key.public_key().public_bytes_raw()
    except ImportError:
        import nacl.bindings
        import nacl.utils
        seed = nacl.utils.random(32)
        public_bytes, private_bytes = nacl.bindings.crypto_sign_seed_keypair(seed)
        private_bytes = private_bytes[:32]

    private_b64 = base64.b64encode(private_bytes).decode()
    public_b64 = base64.b64encode(public_bytes).decode()

    print("=== Ed25519 金鑰對 ===")
    print(f"公開金鑰（放入 license.py 的 _LICENSE_PUBLIC_KEY_B64）：\n{public_b64}")
    print(f"\n私人金鑰（僅管理員持有，不可外流）：\n{private_b64}")
    print(f"\n設定方式：set BETGUARD_LICENSE_PRIVATE_KEY={private_b64}")
    print(f"或儲存至：{_PRIVATE_KEY_PATH}")
    return private_b64, public_b64


# ── Shared code (imported from license module where possible) ──

_EPOCH = date(2024, 1, 1)
_PLAN_BYTES = {"trial_7d": 0x07, "trial_30d": 0x1E}
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


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
        idx = _B32.find(ch)
        if idx < 0:
            raise ValueError(f"Invalid base32 char: {ch}")
        bits = (bits << 5) | idx
        bit_count += 5
        while bit_count >= 8:
            bit_count -= 8
            result.append((bits >> bit_count) & 0xFF)
    return bytes(result)


def _parse_request_code(code: str) -> tuple[str, int] | None:
    """Parse a license request code. Returns (device_hash_hex, timestamp) or None."""
    try:
        cleaned = code.strip().upper()
        if cleaned.startswith("BRQ-"):
            cleaned = cleaned[4:]
        cleaned = cleaned.replace("-", "")
        raw = _b32_decode(cleaned)
        if len(raw) != 36:
            return None
        device_hash = raw[:32].hex()
        ts = struct.unpack(">I", raw[32:36])[0]
        return device_hash, ts
    except Exception:
        return None


def issue_from_request(request_code: str, days: int, plan: str) -> str:
    """
    Issue an Ed25519-signed activation code from a client request code.
    Returns a BG7E-... or BG30E-... code.
    """
    parsed = _parse_request_code(request_code)
    if not parsed:
        raise ValueError("無效的授權申請碼")

    device_hash, ts = parsed
    license_id = os.urandom(4)
    plan_byte = _PLAN_BYTES[plan]
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(days=days)
    issued_day = (issued.date() - _EPOCH).days
    expires_day = (expires.date() - _EPOCH).days
    dev_prefix = bytes.fromhex(device_hash)[:4]

    payload = struct.pack(">4sBHH4s", license_id, plan_byte, issued_day, expires_day, dev_prefix)

    private_key = _load_private_key()
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes(private_key)
        signature = key.sign(payload)
    except ImportError:
        import nacl.bindings
        from betguard.license import _get_public_key_bytes
        pub = _get_public_key_bytes()
        signature = nacl.bindings.crypto_sign_detached(payload, private_key + pub)

    encoded = _b32_encode(payload + signature)
    chunks = [encoded[i:i + 4] for i in range(0, len(encoded), 4)]
    prefix_map = {"trial_7d": "BG7E-", "trial_30d": "BG30E-"}
    return prefix_map.get(plan, "BGXE-") + "-".join(chunks)


def issue_unbound(days: int, plan: str) -> str:
    """Issue an unbound activation code (no device binding). Returns BG7U-... or BG30U-..."""
    from betguard.license import _issue_unbound
    return _issue_unbound(days, plan)


# ── CLI ──

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m betguard.license_issuer",
        description="Admin license issuer (Ed25519-signed codes)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue_cmd = sub.add_parser("issue", help="Issue device-bound code from request code")
    issue_cmd.add_argument("--request-code", required=True)
    issue_cmd.add_argument("--days", type=int, required=True)
    issue_cmd.add_argument("--plan", required=True, choices=["trial_7d", "trial_30d"])

    unbound_cmd = sub.add_parser("issue-unbound", help="Issue unbound code (no BRQ needed)")
    unbound_cmd.add_argument("--days", type=int, required=True)
    unbound_cmd.add_argument("--plan", required=True, choices=["trial_7d", "trial_30d"])

    gen_cmd = sub.add_parser("generate-key", help="Generate a new Ed25519 keypair")

    args = parser.parse_args()

    if args.command == "issue":
        try:
            print(issue_from_request(args.request_code, args.days, args.plan))
        except Exception as e:
            print(f"錯誤：{e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "issue-unbound":
        try:
            print(issue_unbound(args.days, args.plan))
        except Exception as e:
            print(f"錯誤：{e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "generate-key":
        generate_keypair()


if __name__ == "__main__":
    main()
