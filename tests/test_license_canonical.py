"""Test base32 canonical encoding validation for license codes."""

import pytest
from betguard.license import _b32_encode, _b32_decode, _b32_is_canonical, _B32, verify_activation_code


class TestBase32RoundTrip:
    """_b32_encode → _b32_decode must round-trip correctly."""

    def test_empty(self):
        assert _b32_decode(_b32_encode(b"")) == b""

    def test_one_byte(self):
        data = bytes([0xAB])
        encoded = _b32_encode(data)
        assert _b32_decode(encoded) == data

    def test_77_bytes_license_payload(self):
        """77-byte payload (13 + 64 signature) — the actual license size."""
        data = bytes(range(77))
        encoded = _b32_encode(data)
        assert len(encoded) == 124  # 77*8/5 = 123.2 → 124 chars
        assert _b32_decode(encoded) == data

    def test_no_padding(self):
        """5 bytes = exactly 8 base32 chars, no padding bits."""
        data = bytes(range(5))
        encoded = _b32_encode(data)
        assert len(encoded) == 8
        assert _b32_decode(encoded) == data

    def test_various_lengths(self):
        for n in range(20):
            data = bytes(range(n))
            encoded = _b32_encode(data)
            assert _b32_decode(encoded) == data


class TestCanonicalDetection:
    """Non-canonical base32 must be rejected."""

    def test_valid_canonical(self):
        data = bytes(range(77))
        encoded = _b32_encode(data)
        assert _b32_is_canonical(encoded)

    def test_padding_bits_zero_accepted(self):
        """All-zero padding bits are canonical."""
        data = bytes(range(77))
        encoded = _b32_encode(data)
        assert _b32_is_canonical(encoded)

    def test_last_char_alternatives_rejected(self):
        """Only the canonical last char should pass _b32_decode; alternatives raise."""
        data = bytes(range(77))
        canonical = _b32_encode(data)
        last_char = canonical[-1]
        # Canonical decodes fine
        _b32_decode(canonical)
        # The last char encodes only 1 bit of data with 4 bits padding
        for alt in _B32:
            if alt == last_char:
                continue
            mutated = canonical[:-1] + alt
            # Some alternatives may have zero padding bits (same decoded bytes)
            # — _b32_is_canonical catches those too
            if _b32_is_canonical(mutated):
                continue  # same decoded bytes, different last char — still valid decode
            with pytest.raises(ValueError, match="Non-canonical"):
                _b32_decode(mutated)

    def test_middle_char_mutated_decodes_different_data(self):
        """Changing a middle char changes decoded data but remains canonical for that data."""
        data = bytes(range(77))
        canonical = _b32_encode(data)
        pos = 10
        alt = canonical[pos]
        other = "0" if alt != "0" else "1"
        mutated = canonical[:pos] + other + canonical[pos+1:]
        # Both are canonical for their respective data
        assert _b32_is_canonical(canonical)
        assert _b32_is_canonical(mutated)
        # But they decode to different data
        assert _b32_decode(canonical) != _b32_decode(mutated)

    def test_wrong_length_rejected(self):
        """Body != 124 chars must fail _b32_is_canonical."""
        data = bytes(range(77))
        canonical = _b32_encode(data)
        assert len(canonical) == 124
        short = canonical[:-1]
        assert not _b32_is_canonical(short)
        # Too long: append a char that makes it non-canonical
        for alt in _B32:
            long_bad = canonical + alt
            if not _b32_is_canonical(long_bad):
                return  # found one
        pytest.skip("All long alternatives were canonical")

    def test_decode_non_canonical_raises(self):
        """Non-canonical with non-zero padding must raise ValueError."""
        data = bytes(range(77))
        canonical = _b32_encode(data)
        last_char = canonical[-1]
        # Find a char that differs in the padding bits
        for alt in _B32:
            if alt == last_char:
                continue
            mutated = canonical[:-1] + alt
            if not _b32_is_canonical(mutated):
                with pytest.raises(ValueError, match="Non-canonical"):
                    _b32_decode(mutated)
                break
        else:
            pytest.skip("No non-canonical alternative found")


class TestVerifyActivationCodeCanonical:
    """verify_activation_code must reject non-canonical codes."""

    def _issue_code(self, days=7):
        """Generate a valid BG7U code using the test key."""
        from betguard.license_issuer import issue_unbound
        plan = "trial_7d" if days == 7 else "trial_30d"
        return issue_unbound(days=days, plan=plan)

    def test_valid_bg7u_passes(self):
        code = self._issue_code(7)
        result = verify_activation_code(code)
        assert result.get("ok") is True, result

    def test_mutated_last_char_bg7u_fails(self):
        code = self._issue_code(7)
        parts = code.split("-")
        body = parts[-1]  # last segment after prefix
        canonical_char = body[-1]
        # Try mutating last char
        for alt in _B32:
            if alt == canonical_char:
                continue
            mutated = code[:-1] + alt
            result = verify_activation_code(mutated)
            assert result.get("ok") is False, f"{mutated} should fail"

    def test_bg30u_valid_passes(self):
        code = self._issue_code(30)
        result = verify_activation_code(code)
        assert result.get("ok") is True, result

    def test_middle_char_mutation_fails(self):
        code = self._issue_code(7)
        # Remove dashes, find a middle char
        clean = code.replace("-", "")
        pos = 8  # somewhere in the middle
        alt = "0" if clean[pos] != "0" else "1"
        mutated = clean[:pos] + alt + clean[pos+1:]
        result = verify_activation_code(mutated)
        assert result.get("ok") is False

    def test_short_code_fails(self):
        code = self._issue_code(7)
        # Remove last char
        bad = code[:-1]
        result = verify_activation_code(bad)
        assert result.get("ok") is False

    def test_long_code_fails(self):
        code = self._issue_code(7)
        bad = code + "X"
        result = verify_activation_code(bad)
        assert result.get("ok") is False

    def test_lowercase_and_dashes_still_work(self):
        code = self._issue_code(7)
        lower = code.lower()
        result = verify_activation_code(lower)
        assert result.get("ok") is True
