import io
import shutil
import pytest
import requests
from PIL import Image, ImageDraw, ImageFont
from ml_engine import safety_signals, extract_image, clean_text
from url_checker import (
    parse_url,
    PolicyBlock,
    public_addresses,
    trusted,
    GuardedSession,
    check_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://[::1]/",
        "https://work.xyz",
        "https://work.top",
        "https://work.tk",
        "file:///etc/passwd",
        "http://a:b@example.com",
        "https://localhost",
        "https://example.com:8080",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_blocked_urls(url):
    with pytest.raises(PolicyBlock):
        parse_url(url)


def test_domain_boundary_and_negation():
    assert trusted("jobs.linkedin.com")
    assert not trusted("linkedin.com.attacker.example")
    assert not safety_signals("Do not pay registration fee. We conduct interviews.")
    assert safety_signals("Pay registration fee for this opportunity.")
    assert "not" in clean_text("Do not pay any fees.")


def test_private_and_mixed_dns(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(PolicyBlock):
        public_addresses("public.example", 443)


def test_redirect_block_before_transport():
    with GuardedSession("example.com", ["93.184.216.34"], 443, "https") as session:
        request = requests.Request("GET", "https://different.example/job").prepare()
        with pytest.raises(PolicyBlock, match="Cross-domain"):
            session.send(request)


def test_same_domain_redirect_and_cross_domain_chain(monkeypatch):
    monkeypatch.setattr("url_checker.public_addresses", lambda *args: ["93.184.216.34"])
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.url = request.url
        response.request = request
        response._content = b""
        response._content_consumed = True
        if request.url.endswith("/start"):
            response.status_code = 302
            response.headers["Location"] = "/job"
        else:
            response.status_code = 200
        return response

    monkeypatch.setattr("url_checker.PinnedAdapter.send", transport)
    result = check_url("https://example.com/start")
    assert result["hops"] == 1 and not result["hard_block"]
    assert calls == ["https://example.com/start", "https://example.com/job"]
    calls.clear()

    def redirect(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.url = request.url
        response.request = request
        response._content = b""
        response._content_consumed = True
        response.status_code = 302
        response.headers["Location"] = "https://attacker.example/job"
        return response

    monkeypatch.setattr("url_checker.PinnedAdapter.send", redirect)
    result = check_url("https://example.com/start")
    assert result["hard_block"] and len(calls) == 1


def test_redirect_loop_unknown(monkeypatch):
    monkeypatch.setattr("url_checker.public_addresses", lambda *a: ["93.184.216.34"])

    def looping(self, request, **kwargs):
        response = requests.Response()
        response.url = request.url
        response.request = request
        response._content = b""
        response._content_consumed = True
        response.status_code = 302
        response.headers["Location"] = "/loop"
        return response

    monkeypatch.setattr("url_checker.PinnedAdapter.send", looping)
    result = check_url("https://example.com/loop")
    assert result["unknown"] and not result["hard_block"]


@pytest.mark.skipif(
    not shutil.which("tesseract"),
    reason="Tesseract must be installed for real OCR integration",
)
def test_real_tesseract_ocr():
    image = Image.new("RGB", (1400, 280), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default(size=34)
    draw.text((30, 35), "Software engineer - Python and SQL", fill="black", font=font)
    draw.text(
        (30, 95), "Interview required. No application fees.", fill="black", font=font
    )
    data = io.BytesIO()
    image.save(data, format="PNG")
    result = extract_image(data.getvalue())
    assert "software engineer" in result["text"].lower()
    assert result["duration_ms"] > 0
