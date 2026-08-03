"""OCR module + web UI endpoint tests."""

from __future__ import annotations

import io
from http.client import HTTPConnection

import pytest
from PIL import Image, ImageDraw, ImageFont

from tests.test_webui_workbench import _free_port, _running_server
from betguard.webui.app import build_workbench_handler
from betguard import ocr as ocr_mod


def _make_text_image(text: str = "539 01 02 03 04 05", size=(600, 200)) -> bytes:
    """Render a white image with black text (digit-heavy, font-independent)."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 40), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestOcrModule:
    def test_extract_text_returns_lines_with_structure(self):
        data = _make_text_image()
        lines = ocr_mod.extract_text(data)
        assert isinstance(lines, list)
        assert len(lines) >= 1
        line = lines[0]
        assert "text" in line and "score" in line and "box" in line
        assert isinstance(line["text"], str)
        assert 0.0 <= line["score"] <= 1.0

    def test_extract_text_joined_contains_digits(self):
        data = _make_text_image()
        joined = ocr_mod.extract_text_joined(data)
        assert isinstance(joined, str)
        # The rendered digits must survive OCR (allow some misreads, but
        # at least some of the original characters should be present).
        assert any(ch in joined for ch in ("539", "01", "02", "03"))

    def test_empty_bytes_raises(self):
        with pytest.raises(ocr_mod.OcrError):
            ocr_mod.extract_text(b"")

    def test_oversize_image_raises(self):
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (ocr_mod.MAX_IMAGE_BYTES + 1024)
        with pytest.raises(ocr_mod.ImageTooLargeError):
            ocr_mod.extract_text(big)

    def test_garbage_bytes_raises_ocr_error(self):
        with pytest.raises(ocr_mod.OcrError):
            ocr_mod.extract_text(b"this is not an image at all" * 100)


class TestOcrWebEndpoint:
    def _multipart_body(self, image_bytes: bytes, filename: str = "test.png") -> tuple[bytes, str]:
        boundary = "----hermes-test-boundary"
        parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            image_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return b"".join(parts), boundary

    def test_get_ocr_returns_form(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/ocr")
            resp = conn.getresponse()
            assert resp.status == 200
            html = resp.read().decode()
            assert "圖片轉文字" in html
            assert "multipart/form-data" in html
            assert 'name="image"' in html
            conn.close()

    def test_post_ocr_without_file_returns_400(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/ocr", body=b"", headers={"Content-Type": "multipart/form-data; boundary=x"})
            resp = conn.getresponse()
            assert resp.status == 400
            conn.close()

    def test_post_ocr_with_image_returns_result_page(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        body, boundary = self._multipart_body(_make_text_image())
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=60)
            conn.request(
                "POST",
                "/ocr",
                body=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
            )
            resp = conn.getresponse()
            assert resp.status == 200
            html = resp.read().decode()
            assert "OCR" in html
            # Either recognized lines (table) or an honest "no text" message.
            assert ("信心度" in html) or ("未辨識到任何文字" in html)
            conn.close()

    def test_dashboard_links_to_ocr(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/")
            resp = conn.getresponse()
            html = resp.read().decode()
            assert "/ocr" in html
            conn.close()
