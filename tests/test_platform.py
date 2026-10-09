"""Integration tests run against a temporary SQLite database and real ML model."""

import io
import time
import pytest
from sqlalchemy import select
from app import create_app
from models import db, User, Feedback, ScanHistory
from auth import hash_password


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "test.db"),
            "MODEL_PATH": tmp_path / "model.joblib",
            "ENABLE_SCHEDULER": False,
            "RATELIMIT_ENABLED": False,
        }
    )
    yield application
    application.extensions["tasks"].pool.shutdown(wait=True)
    application.extensions["retrainer"].shutdown()
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email="member@example.com"):
    response = client.post(
        "/api/auth/register",
        json={"name": "Test Member", "email": email, "password": "A-test-password-123"},
    )
    assert response.status_code == 201, response.json
    return response.json


def headers(client):
    return {"X-CSRF-Token": client.get_cookie("csrf_token").value}


def scan(client, **payload):
    response = client.post("/api/scans", json=payload, headers=headers(client))
    assert response.status_code == 202, response.json
    for _ in range(100):
        status = client.get("/api/jobs/" + response.json["id"]).json
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(0.03)
    assert status["status"] == "completed", status
    return status["result"]


def test_register_hash_csrf_and_logout(app, client):
    register(client)
    with app.app_context():
        assert db.session.scalar(select(User)).password_hash.startswith(
            "pbkdf2:sha256:600000$"
        )
    assert client.post("/api/scans", json={"text": "hello"}).status_code == 401
    token = client.get_cookie("access_token").value
    assert (
        client.post("/api/auth/logout", json={}, headers=headers(client)).status_code
        == 200
    )
    assert (
        client.get("/api/me", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )


def test_login_and_invalid_fields(client):
    register(client)
    assert (
        client.post(
            "/api/auth/login", json={"email": "member@example.com", "password": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "member@example.com", "password": "A-test-password-123"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"name": "x", "email": "bad", "password": "short"},
        ).status_code
        == 400
    )
    assert (
        client.post("/api/scans", json=[], headers=headers(client)).status_code == 400
    )


def test_low_risk_text(client):
    register(client)
    result = scan(
        client,
        text="Software engineer with relevant experience. Apply on our company website. Salary and benefits are documented. Interview with the hiring manager. No application fees are charged.",
    )
    assert result["verdict"] == "real"
    assert result["confidence"] is not None
    assert client.get("/api/stats").json["total"] == 1


def test_safety_override(client):
    register(client)
    result = scan(
        client,
        text="Urgent data entry job. Pay registration fee today. No interview required.",
    )
    assert result["verdict"] == "fake" and result["override"]
    assert result["confidence"] is None


def test_link_block_skips_ml(app, client, monkeypatch):
    register(client)
    monkeypatch.setattr(
        app.extensions["engine"], "predict", lambda _: pytest.fail("ML should not run")
    )
    result = scan(
        client,
        text="A full software engineering job description with benefits.",
        url="https://job-awards.xyz/opening",
    )
    assert result["verdict"] == "fake" and result["override"]


def test_network_unknown_never_safe(client, monkeypatch):
    register(client)
    monkeypatch.setattr(
        "app.check_url",
        lambda _: dict(
            hard_block=False,
            unknown=True,
            risk=None,
            domain="company.example",
            reasons=["Unreachable"],
        ),
    )
    result = scan(client, url="https://company.example/job")
    assert result["verdict"] == "review" and result["confidence"] is None


def test_combined_and_uncertain_text(client):
    register(client)
    result = scan(
        client,
        text="Software engineer interview with the hiring manager.",
        image_text="Written contract, documented salary, health benefits and paid leave. Company careers portal.",
    )
    assert result["source"] == "Text + Image"
    assert result["confidence"] is not None
    assert scan(client, text="xyzzy qqqqq")["verdict"] == "review"


def test_ownership_feedback_and_rbac(app, client):
    register(client)
    result = scan(
        client,
        text="Software engineer needs an interview and a written contract. Salary depends on relevant experience.",
    )
    response = client.post(
        f"/api/scans/{result['id']}/feedback",
        json={"label": "real"},
        headers=headers(client),
    )
    assert response.status_code == 201
    assert (
        client.post(
            f"/api/scans/{result['id']}/feedback",
            json={"label": "fake"},
            headers=headers(client),
        ).status_code
        == 409
    )
    other = app.test_client()
    register(other, "other@example.com")
    assert other.get(f"/api/scans/{result['id']}").status_code == 404
    assert (
        other.post(
            f"/api/scans/{result['id']}/feedback",
            json={"label": "fake"},
            headers=headers(other),
        ).status_code
        == 404
    )
    assert other.get("/api/admin/stats").status_code == 403
    assert other.get("/api/scans").json["total"] == 0


def test_retraining_hot_swap_and_threshold(app, client, monkeypatch):
    register(client)
    result = scan(
        client,
        text="Software engineer with Python skills. Two interviews, a salary range, and formal benefits. No charges.",
    )
    client.post(
        f"/api/scans/{result['id']}/feedback",
        json={"label": "real"},
        headers=headers(client),
    )
    worker = app.extensions["retrainer"]
    before = app.extensions["engine"].metadata()["version"]
    app.config["FEEDBACK_THRESHOLD"] = 1
    requested = []
    monkeypatch.setattr(worker, "request", lambda: requested.append(True))
    worker.check_threshold()
    assert requested == [True]
    worker.run()
    assert worker.status == "completed", worker.error
    assert app.extensions["engine"].metadata()["version"] != before
    with app.app_context():
        assert db.session.scalar(select(Feedback)).trained
    assert (
        scan(
            client,
            text="Python developer, interview, written contract and relevant experience.",
        )["model_version"]
        != before
    )


def test_admin_roles_and_delete(app, client):
    with app.app_context():
        db.session.add(
            User(
                email="admin@example.com",
                name="Admin",
                role="admin",
                password_hash=hash_password("Admin-password-123"),
            )
        )
        db.session.commit()
    client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-123"},
    )
    user_client = app.test_client()
    member = register(user_client)
    scan(user_client, text="Pay registration fee for immediate employment.")
    assert client.get("/api/admin/stats").status_code == 200
    assert (
        client.patch(
            "/api/admin/users/" + str(member["id"]),
            json={"role": "admin"},
            headers=headers(client),
        ).status_code
        == 200
    )
    assert user_client.get("/api/me").status_code == 401
    assert (
        client.patch(
            "/api/admin/users/1", json={"role": "user"}, headers=headers(client)
        ).status_code
        == 409
    )
    assert (
        client.delete(
            "/api/admin/users/" + str(member["id"]), headers=headers(client)
        ).status_code
        == 200
    )
    with app.app_context():
        assert db.session.scalar(select(ScanHistory)) is None


def test_bad_image_and_empty_input(client):
    register(client)
    assert (
        client.post("/api/scans", json={}, headers=headers(client)).status_code == 400
    )
    r = client.post(
        "/api/ocr",
        data={"image": (io.BytesIO(b"not a real image"), "fake.png")},
        headers=headers(client),
    )
    assert r.status_code == 202
    for _ in range(100):
        job = client.get("/api/jobs/" + r.json["id"]).json
        if job["status"] == "failed":
            break
        time.sleep(0.02)
    assert job["status"] == "failed"


def test_cross_site_and_templates(client):
    assert (
        client.post(
            "/api/auth/register",
            json={},
            headers={"Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert client.get("/dashboard").status_code == 302
    register(client)
    assert b"Check your next opportunity" in client.get("/dashboard").data
    assert client.get("/health").headers["X-Content-Type-Options"] == "nosniff"


def test_actual_background_schedule(app):
    worker = app.extensions["retrainer"]
    before = app.extensions["engine"].metadata()["version"]
    worker.start()
    try:
        assert (
            worker.scheduler.get_job("daily-retrain").trigger.interval.total_seconds()
            == 86400
        )
        assert app.config["FEEDBACK_THRESHOLD"] == 50
        assert worker.request()
        for _ in range(100):
            if worker.status in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert worker.status == "completed", worker.error
        assert app.extensions["engine"].metadata()["version"] != before
    finally:
        worker.shutdown()


def test_cloud_admin_bootstrap(tmp_path, monkeypatch):
    from auth import verify_password

    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Cloud-password-123")
    config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "cloud.db"),
        "MODEL_PATH": tmp_path / "model.joblib",
        "ENABLE_SCHEDULER": False,
        "RATELIMIT_ENABLED": False,
    }
    first = create_app(config)
    try:
        with first.app_context():
            admin = db.session.scalar(
                select(User).where(User.email == "owner@example.com")
            )
            assert admin.role == "admin"
            assert verify_password(admin.password_hash, "Cloud-password-123")
            admin.role = "user"
            db.session.commit()
        monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Changed-password-456")
        second = create_app(config)
        try:
            with second.app_context():
                users = list(db.session.scalars(select(User)))
                assert len(users) == 1
                assert (
                    users[0].role == "user"
                )  # Startup never escalates existing accounts.
                assert verify_password(users[0].password_hash, "Cloud-password-123")
        finally:
            second.extensions["tasks"].pool.shutdown(wait=True)
    finally:
        first.extensions["tasks"].pool.shutdown(wait=True)


def test_cloud_admin_requires_both_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        create_app(
            {
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "invalid.db"),
                "ENABLE_SCHEDULER": False,
            }
        )
