"""Optional isolated browser test. Run: python tests/smoke_browser.py
First install browser: python -m playwright install chromium
"""

import argparse, json, secrets, sys, tempfile, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--output", default="previews")
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(temp / "test.db"),
            "MODEL_PATH": temp / "model.joblib",
            "ENABLE_SCHEDULER": True,
            "RATELIMIT_ENABLED": False,
        }
    )
    password = secrets.token_urlsafe(24)
    result = app.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--email",
            "admin@example.com",
            "--name",
            "Platform Admin",
            "--password",
            password,
        ]
    )
    assert result.exit_code == 0, result.output
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_port}"
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(
                viewport={"width": 1512, "height": 1100}, device_scale_factor=1
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address + "/login", wait_until="networkidle")
            page.click("#auth-toggle")
            page.fill("#name", "Alex Morgan")
            page.fill("#email", "member@example.com")
            page.fill("#password", password)
            page.click("#auth-submit")
            page.wait_for_url("**/dashboard")
            page.wait_for_function(
                "typeof Chart !== 'undefined' && typeof Alpine !== 'undefined'"
            )
            page.click("#example")
            page.click("#scan-button")
            page.wait_for_selector("#result:not([hidden])", timeout=30000)
            assert "High-risk" in page.inner_text("#verdict")
            page.fill(
                "#job-text",
                "Software engineer with Python and SQL skills. Apply through our official careers portal. Interview with a hiring manager. Salary and benefits are documented. No application fees.",
            )
            page.click("#scan-button")
            page.wait_for_selector("#result:not([hidden])", timeout=30000)
            assert "No strong" in page.inner_text("#verdict")
            page.click("#feedback-box summary")
            page.click("#feedback-button")
            page.wait_for_function(
                "document.querySelector('#feedback-button').textContent === 'Correction received'"
            )
            img = Image.new("RGB", (1400, 240), "white")
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 32)
            except OSError:
                font = ImageFont.load_default(size=32)
            draw.text(
                (25, 35),
                "Software engineer with Python experience.",
                fill="black",
                font=font,
            )
            draw.text(
                (25, 100),
                "Interview required. Salary and benefits documented.",
                fill="black",
                font=font,
            )
            image_path = temp / "job.png"
            img.save(image_path)
            page.click('[data-tab="image"]')
            page.set_input_files("#job-image", str(image_path))
            page.wait_for_function(
                "document.querySelector('#image-status').textContent.startsWith('Text extracted')",
                timeout=30000,
            )
            assert "engineer" in page.input_value("#extracted").lower()
            page.click("#clear-image")
            page.click('[data-tab="text"]')
            page.evaluate(
                "document.querySelector('#result').hidden=true; document.querySelector('#toast').hidden=true"
            )
            page.fill("#job-text", "")
            page.evaluate(
                "document.activeElement.blur();window.scrollTo(0,document.body.scrollHeight)"
            )
            page.wait_for_timeout(650)
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(650)
            page.screenshot(path=str(output / "dashboard-preview.png"), full_page=True)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=window.innerWidth"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=window.innerWidth"
            )
            page.set_viewport_size({"width": 1512, "height": 1100})
            page.click(".profile")
            page.wait_for_url("**/login")
            page.fill("#email", "admin@example.com")
            page.fill("#password", password)
            page.click("#auth-submit")
            page.wait_for_url("**/dashboard")
            page.goto(address + "/admin", wait_until="networkidle")
            page.wait_for_function(
                "document.querySelector('#global-total').textContent === '2'"
            )
            before = page.inner_text("#model-version")
            page.click("#retrain")
            page.wait_for_function(
                "document.querySelector('#training-state').textContent === 'completed'",
                timeout=30000,
            )
            assert page.inner_text("#model-version") != before
            page.once("dialog", lambda dialog: dialog.accept())
            page.click("[data-role]")
            page.wait_for_function(
                "document.querySelector('[data-role]').dataset.next === 'user'"
            )
            page.once("dialog", lambda dialog: dialog.accept())
            page.click("[data-role]")
            page.wait_for_function(
                "document.querySelector('[data-role]').dataset.next === 'admin'"
            )
            page.evaluate(
                "document.querySelector('#toast').hidden=true;window.scrollTo(0,document.body.scrollHeight)"
            )
            page.wait_for_timeout(650)
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(650)
            page.screenshot(path=str(output / "admin-preview.png"), full_page=True)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=window.innerWidth"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(400)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=window.innerWidth"
            )
            health = page.evaluate("fetch('/api/admin/stats').then(r=>r.json())")
            report = {
                "browser_errors": errors,
                "flows": [
                    "register",
                    "text scan",
                    "safety rule",
                    "feedback",
                    "real OCR upload",
                    "history",
                    "charts",
                    "mobile layouts",
                    "logout",
                    "admin login",
                    "background retraining",
                    "promote and demote",
                ],
                "measured_text_p95_ms": health["text_p95"],
                "measured_image_p95_ms": health["image_p95"],
                "synthetic_model_metrics": health["model"]["metrics"],
            }
            (output / "browser-validation.json").write_text(
                json.dumps(report, indent=2)
            )
            print(json.dumps(report))
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        app.extensions["retrainer"].shutdown()
        app.extensions["tasks"].pool.shutdown(wait=True)
