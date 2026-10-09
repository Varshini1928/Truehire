"""TrueHire Flask application. Run `python app.py` or use the supplied Dockerfile."""

import atexit
import logging
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
import click
import numpy as np
import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify, request, render_template, redirect, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from models import db, User, ScanHistory, Feedback, Job, AuditLog, utcnow, iso
from auth import hash_password, verify_password, issue_session, require_auth, load_user
from ml_engine import ModelEngine, extract_image, safety_signals, WORDNET_READY
from url_checker import check_url
from scheduler import Retrainer
from train_model import initialize

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


class QueueFull(Exception):
    pass


class Tasks:
    def __init__(self, app):
        self.app = app
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="truehire")
        self.capacity = threading.BoundedSemaphore(24)
        atexit.register(lambda: self.pool.shutdown(wait=False, cancel_futures=True))

    def submit(self, user_id, kind, fn):
        if not self.capacity.acquire(blocking=False):
            raise QueueFull()
        identifier = str(uuid.uuid4())
        try:
            db.session.add(
                Job(
                    id=identifier,
                    user_id=user_id,
                    kind=kind,
                    logs=[{"message": "Queued for analysis", "level": "info"}],
                )
            )
            db.session.commit()
            self.pool.submit(self.execute, identifier, fn)
        except Exception:
            self.capacity.release()
            raise
        return identifier

    def execute(self, identifier, fn):
        with self.app.app_context():

            def update(message, level="info"):
                job = db.session.get(Job, identifier)
                if job is None:
                    raise ValueError("This task is no longer available.")
                job.status = "running"
                job.logs = [*job.logs, {"message": message, "level": level}]
                db.session.commit()

            try:
                result = fn(update)
                job = db.session.get(Job, identifier)
                if job:
                    job.result = result
                    job.status = "completed"
                    db.session.commit()
            except Exception as exc:
                db.session.rollback()
                self.app.logger.exception("Background task %s failed", identifier)
                job = db.session.get(Job, identifier)
                if job:
                    job.status = "failed"
                    job.error = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "Processing failed. Try again or contact your administrator."
                    )
                    db.session.commit()
            finally:
                self.capacity.release()


def create_app(test_config=None):
    instance = Path(os.environ.get("INSTANCE_DIR", ROOT / "instance")).resolve()
    instance.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__, instance_path=str(instance))
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        # Local setup is immediately runnable without an insecure hard-coded key.
        keyfile = instance / ".secret"
        if not keyfile.exists():
            try:
                fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_hex(32))
            except FileExistsError:
                pass
        secret = keyfile.read_text().strip()
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters.")
    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///" + str(instance / "truehire.db")
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        MODEL_PATH=instance / "model.joblib",
        DATA_DIR=ROOT / "data",
        FEEDBACK_THRESHOLD=50,
        ENABLE_SCHEDULER=os.environ.get("ENABLE_SCHEDULER", "true").lower() == "true",
        RATELIMIT_STORAGE_URI="memory://",
        RATELIMIT_DEFAULT="300 per hour",
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=["300 per hour"],
        storage_uri="memory://",
    )
    app.extensions["limiter_instance"] = limiter
    with app.app_context():
        db.create_all()
        # Optional cloud bootstrap: free Render services have no shell access.
        # Credentials come only from host secrets, never from source defaults.
        admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if admin_email or admin_password:
            if (
                not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", admin_email)
                or len(admin_email) > 254
                or not 12 <= len(admin_password) <= 128
            ):
                raise RuntimeError(
                    "Set a valid BOOTSTRAP_ADMIN_EMAIL and a BOOTSTRAP_ADMIN_PASSWORD of 12–128 characters."
                )
            existing_admin = db.session.scalar(
                select(User).where(User.email == admin_email)
            )
            if existing_admin is None:
                db.session.add(
                    User(
                        email=admin_email,
                        name=os.environ.get(
                            "BOOTSTRAP_ADMIN_NAME", "Administrator"
                        ).strip()[:80]
                        or "Administrator",
                        password_hash=hash_password(admin_password),
                        role="admin",
                    )
                )
                db.session.commit()
            # Existing accounts are never promoted or reset by startup.
        if not Path(app.config["MODEL_PATH"]).exists():
            initialize(app.config["DATA_DIR"], app.config["MODEL_PATH"])
        engine = ModelEngine(app.config["MODEL_PATH"])
        app.extensions["engine"] = engine
        # In-process work cannot survive restart; make interrupted jobs explicit.
        if app.config["ENABLE_SCHEDULER"]:
            db.session.execute(
                db.update(Job)
                .where(Job.status.in_(["queued", "running"]))
                .values(
                    status="failed",
                    error="Server restarted before processing finished. Please submit again.",
                )
            )
        ids = engine.metadata().get("feedback_ids", [])
        if ids:
            db.session.execute(
                db.update(Feedback).where(Feedback.id.in_(ids)).values(trained=True)
            )
        db.session.commit()
    tasks = Tasks(app)
    app.extensions["tasks"] = tasks
    retrainer = Retrainer(app, engine)
    app.extensions["retrainer"] = retrainer
    if app.config["ENABLE_SCHEDULER"]:
        retrainer.start()

    @app.before_request
    def same_origin_mutations():
        # Protect login/register against cross-site form submission too. Cookie
        # mutations additionally require the token-bound CSRF header in auth.py.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="Cross-site requests are not permitted."), 403
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="Origin mismatch."), 403
            if (
                request.method in ("POST", "PUT", "PATCH")
                and request.path.startswith("/api/")
                and request.path != "/api/ocr"
                and not request.is_json
            ):
                return jsonify(error="Send application/json."), 415

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Cache-Control"] = "no-store"
        # Tailwind CDN and Alpine's standard build require these inline/eval
        # allowances. For strict CSP, compile Tailwind and use Alpine CSP build.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if app.config["COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        db.session.rollback()
        app.logger.exception("Unhandled request error")
        return jsonify(error="Unexpected server error. Please try again."), 500

    @app.get("/")
    def home():
        return redirect("/dashboard" if load_user() else "/login")

    @app.get("/login")
    def login_page():
        return render_template("auth.html")

    @app.get("/dashboard")
    @require_auth(page=True)
    def dashboard():
        return render_template("dashboard.html", title="Overview", user=g.user)

    @app.get("/admin")
    @require_auth(admin=True, page=True)
    def admin_page():
        return render_template("admin.html", title="Control center", user=g.user)

    @app.get("/health")
    @limiter.exempt
    def health():
        return jsonify(status="ok", model_ready=True)

    @app.post("/api/auth/register")
    @limiter.limit("5 per minute")
    def register():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 400
        name = payload.get("name", "")
        email = payload.get("email", "")
        password = payload.get("password", "")
        if not all(isinstance(v, str) for v in (name, email, password)):
            return jsonify(error="Invalid registration fields."), 400
        name = name.strip()
        email = email.strip().lower()
        if (
            not 1 <= len(name) <= 80
            or len(email) > 254
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
            or not 12 <= len(password) <= 128
        ):
            return (
                jsonify(
                    error="Enter a name, valid email, and a password of 12–128 characters."
                ),
                400,
            )
        user = User(name=name, email=email, password_hash=hash_password(password))
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="This account cannot be created. Try signing in."), 409
        return (
            issue_session(jsonify(id=user.id, name=user.name, role=user.role), user),
            201,
        )

    @app.post("/api/auth/login")
    @limiter.limit("10 per minute")
    def login():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 400
        email = payload.get("email", "")
        password = payload.get("password", "")
        if (
            not isinstance(email, str)
            or not isinstance(password, str)
            or len(password) > 128
        ):
            return jsonify(error="Invalid credentials."), 401
        user = db.session.scalar(
            select(User).where(User.email == email.strip().lower())
        )
        # Perform the same expensive hash check for unknown accounts.
        stored = user.password_hash if user else app.config["DUMMY_PASSWORD_HASH"]
        valid = verify_password(stored, password)
        if not user or not valid:
            return jsonify(error="Invalid email or password."), 401
        return issue_session(jsonify(id=user.id, name=user.name, role=user.role), user)

    app.config["DUMMY_PASSWORD_HASH"] = hash_password(secrets.token_urlsafe(24))

    @app.post("/api/auth/logout")
    @require_auth()
    def logout():
        g.user.token_version += 1
        db.session.commit()
        response = jsonify(ok=True)
        response.delete_cookie("access_token", path="/")
        response.delete_cookie("csrf_token", path="/")
        return response

    @app.get("/api/me")
    @require_auth()
    def me():
        return jsonify(
            id=g.user.id, name=g.user.name, email=g.user.email, role=g.user.role
        )

    @app.post("/api/ocr")
    @limiter.limit("20 per hour")
    @require_auth()
    def ocr():
        upload = request.files.get("image")
        if not upload:
            return jsonify(error="Attach an image."), 400
        data = upload.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            return jsonify(error="Image must be at most 5 MB."), 413

        def work(log):
            log("Extracting text from screenshot…")
            result = extract_image(data)
            log("OCR complete. Preview the extracted text.")
            return result

        try:
            identifier = tasks.submit(g.user.id, "ocr", work)
        except QueueFull:
            return jsonify(error="Processing queue is full. Try again shortly."), 503
        return jsonify(id=identifier), 202

    @app.get("/api/jobs/<identifier>")
    @limiter.exempt
    @require_auth()
    def job_status(identifier):
        job = db.session.get(Job, identifier)
        if not job or job.user_id != g.user.id:
            return jsonify(error="Task not found."), 404
        return jsonify(
            id=job.id,
            status=job.status,
            logs=job.logs,
            result=job.result,
            error=job.error,
        )

    @app.post("/api/scans")
    @limiter.limit("30 per hour")
    @require_auth()
    def create_scan():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 400
        values = [payload.get(k, "") for k in ("text", "image_text", "url")]
        if not all(isinstance(v, str) for v in values):
            return jsonify(error="Inputs must be strings."), 400
        text, image_text, url = [v.strip() for v in values]
        if len(text) > 20000 or len(image_text) > 20000 or len(url) > 2048:
            return jsonify(error="Input exceeds supported length."), 400
        if not any((text, image_text, url)):
            return jsonify(error="Provide text, screenshot text, or a URL."), 400
        ocr_ms = None
        if payload.get("ocr_job_id"):
            if not isinstance(payload["ocr_job_id"], str):
                return jsonify(error="Invalid OCR task."), 400
            job = db.session.get(Job, payload["ocr_job_id"])
            if (
                not job
                or job.user_id != g.user.id
                or job.kind != "ocr"
                or job.status != "completed"
            ):
                return jsonify(error="OCR task is unavailable."), 400
            ocr_ms = job.result.get("duration_ms")
        user_id = g.user.id

        def work(log):
            start = time.perf_counter()
            reasons = []
            link = None
            combined = "\n\n".join(t for t in (text, image_text) if t)
            version = engine.metadata()["version"]
            confidence = None
            risk = None
            text_ms = None
            override = False
            if url:
                log("Checking link reputation and redirects…")
                link = check_url(url)
                reasons.extend(link["reasons"])
                log(
                    (
                        "Suspicious redirect or destination detected."
                        if link["hard_block"]
                        else "Link inspection complete."
                    ),
                    "warning" if link["hard_block"] or link["unknown"] else "info",
                )
            # URL policy is evaluated FIRST. A blocked URL never waits for ML.
            if link and link["hard_block"]:
                verdict = "fake"
                override = True
                risk = 100.0
            else:
                signals = safety_signals(combined)
                if signals:
                    reasons.extend(signals)
                    verdict = "fake"
                    override = True
                    risk = 100.0
                    log("High-risk language rule matched.", "warning")
                else:
                    predictions = []
                    if combined:
                        log("Cleaning, tokenizing, and analyzing semantics…")
                        inference_start = time.perf_counter()
                        for content, weight in ((text, 0.6), (image_text, 0.4)):
                            if content:
                                prediction = engine.predict(content)
                                version = prediction["version"]
                                if prediction["probability"] is not None:
                                    predictions.append(
                                        (prediction["probability"], weight)
                                    )
                        text_ms = (time.perf_counter() - inference_start) * 1000
                    if predictions:
                        # Text and OCR use 60/40 weights when both are present.
                        # Available channels are renormalized. A successful link
                        # inspection adds 20%; unavailable links never imply safety.
                        p = sum(p * w for p, w in predictions) / sum(
                            w for _, w in predictions
                        )
                        if link and link["risk"] is not None:
                            p = 0.8 * p + 0.2 * link["risk"]
                        risk = round(100 * p, 1)
                        if 0.40 <= p <= 0.60 or (link and link["unknown"]):
                            verdict = "review"
                        else:
                            verdict = "fake" if p > 0.60 else "real"
                        confidence = round(100 * max(p, 1 - p), 1)
                        reasons.append(
                            "Language model found elevated fraud patterns."
                            if p > 0.60
                            else (
                                "No dominant fraud pattern found in the supplied language."
                                if p < 0.40
                                else "The language model is uncertain."
                            )
                        )
                    else:
                        verdict = "review"
                        reasons.append(
                            "Insufficient recognizable job text for ML prediction. A URL alone cannot verify a job."
                        )
                    log("Combined available evidence.")
            source = " + ".join(
                name
                for name, present in [
                    ("Text", text),
                    ("Image", image_text),
                    ("URL", url),
                ]
                if present
            )
            scan = ScanHistory(
                user_id=user_id,
                text=combined,
                url=url,
                domain=link["domain"] if link else "",
                verdict=verdict,
                confidence=confidence,
                risk_score=risk,
                override=override,
                reasons=reasons,
                source=source,
                model_version=version,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
                text_ms=text_ms,
                image_ms=ocr_ms,
            )
            db.session.add(scan)
            db.session.commit()
            log("Assessment saved to your history.")
            return scan.public()

        try:
            identifier = tasks.submit(user_id, "scan", work)
        except QueueFull:
            return jsonify(error="Processing queue is full. Try again shortly."), 503
        return jsonify(id=identifier), 202

    def pagination():
        try:
            return max(1, min(int(request.args.get("page", 1)), 100000))
        except ValueError:
            return 1

    @app.get("/api/scans")
    @require_auth()
    def scans():
        query = select(ScanHistory).where(ScanHistory.user_id == g.user.id)
        verdict = request.args.get("verdict", "")
        if verdict in ("real", "fake", "review"):
            query = query.where(ScanHistory.verdict == verdict)
        term = request.args.get("q", "")[:200]
        if term:
            query = query.where(
                or_(
                    ScanHistory.text.contains(term, autoescape=True),
                    ScanHistory.url.contains(term, autoescape=True),
                )
            )
        data = db.paginate(
            query.order_by(ScanHistory.created_at.desc()),
            page=pagination(),
            per_page=10,
            error_out=False,
        )
        return jsonify(
            items=[s.public() for s in data.items],
            total=data.total,
            pages=max(1, data.pages),
        )

    @app.get("/api/scans/<int:identifier>")
    @require_auth()
    def scan_detail(identifier):
        scan = db.session.get(ScanHistory, identifier)
        if not scan or scan.user_id != g.user.id:
            return jsonify(error="Scan not found."), 404
        feedback = db.session.scalar(
            select(Feedback).where(Feedback.scan_id == identifier)
        )
        return jsonify(
            scan.public() | {"feedback": feedback.label if feedback else None}
        )

    @app.post("/api/scans/<int:identifier>/feedback")
    @limiter.limit("20 per hour")
    @require_auth()
    def feedback(identifier):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 400
        label = payload.get("label")
        if label not in ("real", "fake"):
            return jsonify(error="Label must be real or fake."), 400
        scan = db.session.get(ScanHistory, identifier)
        if not scan or scan.user_id != g.user.id:
            return jsonify(error="Scan not found."), 404
        if not scan.text.strip():
            return (
                jsonify(error="Text is required to provide a classifier correction."),
                400,
            )
        entry = Feedback(scan_id=identifier, user_id=g.user.id, label=label)
        db.session.add(entry)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return (
                jsonify(error="A correction was already submitted for this scan."),
                409,
            )
        if app.config["ENABLE_SCHEDULER"]:
            retrainer.check_threshold()
        return jsonify(ok=True), 201

    def aggregate(user_id=None):
        conditions = [ScanHistory.user_id == user_id] if user_id is not None else []
        counts = dict(
            db.session.execute(
                select(ScanHistory.verdict, func.count())
                .where(*conditions)
                .group_by(ScanHistory.verdict)
            ).all()
        )
        total = sum(counts.values())
        fake = counts.get("fake", 0)
        average = (
            db.session.scalar(
                select(func.avg(ScanHistory.duration_ms)).where(*conditions)
            )
            or 0
        )
        return dict(
            total=total,
            real=counts.get("real", 0),
            fake=fake,
            review=counts.get("review", 0),
            fraud_rate=round(fake / total * 100, 1) if total else 0,
            average_ms=round(average, 1),
        )

    @app.get("/api/stats")
    @require_auth()
    def stats():
        data = aggregate(g.user.id)
        today = utcnow().date()
        begin = today - timedelta(days=13)
        rows = dict(
            db.session.execute(
                select(func.date(ScanHistory.created_at), func.count())
                .where(
                    ScanHistory.user_id == g.user.id, ScanHistory.created_at >= begin
                )
                .group_by(func.date(ScanHistory.created_at))
            ).all()
        )
        recent = list(
            db.session.scalars(
                select(ScanHistory.verdict)
                .where(ScanHistory.user_id == g.user.id)
                .order_by(ScanHistory.id.desc())
                .limit(20)
            )
        )
        data["recent_rate"] = (
            round(recent.count("fake") / len(recent) * 100, 1) if recent else 0
        )
        data["daily"] = [
            dict(
                date=(begin + timedelta(days=n)).isoformat(),
                count=rows.get((begin + timedelta(days=n)).isoformat(), 0),
            )
            for n in range(14)
        ]
        return jsonify(data)

    @app.get("/api/admin/stats")
    @require_auth(admin=True)
    def admin_stats():
        data = aggregate()
        data.update(
            cpu_percent=psutil.cpu_percent(interval=0.1),
            ram_percent=psutil.virtual_memory().percent,
            model=engine.metadata(),
            training=retrainer.info(),
            ocr_available=shutil.which(os.environ.get("TESSERACT_CMD", "tesseract"))
            is not None,
        )
        text_values = list(
            db.session.scalars(
                select(ScanHistory.text_ms)
                .where(ScanHistory.text_ms.isnot(None))
                .order_by(ScanHistory.id.desc())
                .limit(100)
            )
        )
        image_values = [
            r[0]["duration_ms"]
            for r in db.session.execute(
                select(Job.result)
                .where(Job.kind == "ocr", Job.status == "completed")
                .order_by(Job.created_at.desc())
                .limit(100)
            )
        ]
        data.update(
            text_p95=float(np.percentile(text_values, 95)) if text_values else None,
            image_p95=float(np.percentile(image_values, 95)) if image_values else None,
        )
        data["domains"] = [
            dict(domain=domain, count=count)
            for domain, count in db.session.execute(
                select(ScanHistory.domain, func.count())
                .where(ScanHistory.verdict == "fake", ScanHistory.domain != "")
                .group_by(ScanHistory.domain)
                .order_by(func.count().desc())
                .limit(8)
            )
        ]
        data["feedback"] = [
            dict(
                scan_id=f.scan_id,
                label=f.label,
                trained=f.trained,
                created_at=iso(f.created_at),
            )
            for f in db.session.scalars(
                select(Feedback).order_by(Feedback.id.desc()).limit(8)
            )
        ]
        data["pending_feedback"] = db.session.scalar(
            select(func.count(Feedback.id)).where(Feedback.trained.is_(False))
        )
        return jsonify(data)

    @app.post("/api/admin/retrain")
    @limiter.limit("4 per hour")
    @require_auth(admin=True)
    def force_retrain():
        if not app.config["ENABLE_SCHEDULER"]:
            return jsonify(error="Scheduler is disabled."), 503
        accepted = retrainer.request()
        db.session.add(AuditLog(actor_id=g.user.id, action="retrain_requested"))
        db.session.commit()
        return jsonify(queued=accepted), 202

    @app.get("/api/admin/users")
    @require_auth(admin=True)
    def users():
        query = select(User)
        term = request.args.get("q", "")[:200]
        if term:
            query = query.where(
                or_(
                    User.email.contains(term, autoescape=True),
                    User.name.contains(term, autoescape=True),
                )
            )
        data = db.paginate(
            query.order_by(User.id), page=pagination(), per_page=20, error_out=False
        )
        return jsonify(
            items=[
                dict(
                    id=u.id,
                    name=u.name,
                    email=u.email,
                    role=u.role,
                    created_at=iso(u.created_at),
                )
                for u in data.items
            ],
            pages=max(1, data.pages),
        )

    @app.route("/api/admin/users/<int:identifier>", methods=["PATCH", "DELETE"])
    @require_auth(admin=True)
    def modify_user(identifier):
        if identifier == g.user.id:
            return (
                jsonify(
                    error="You cannot change or delete your own administrator account."
                ),
                409,
            )
        user = db.session.get(User, identifier)
        if not user:
            return jsonify(error="User not found."), 404
        if request.method == "DELETE":
            # FK cascades remove private scans, jobs, and corrections.
            db.session.delete(user)
            action = "user_deleted"
        else:
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict) or payload.get("role") not in (
                "user",
                "admin",
            ):
                return jsonify(error="Role must be user or admin."), 400
            user.role = payload["role"]
            user.token_version += 1
            action = "role_changed_" + user.role
        db.session.add(
            AuditLog(actor_id=g.user.id, target_id=identifier, action=action)
        )
        db.session.commit()
        return jsonify(ok=True)

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(email, name, password):
        """Create the first admin interactively; never ship default credentials."""
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not name.strip():
            raise click.ClickException("A name and valid email are required.")
        if db.session.scalar(select(User).where(User.email == email.strip().lower())):
            raise click.ClickException(
                "Account already exists. Use an authorized admin to change roles."
            )
        db.session.add(
            User(
                email=email.strip().lower(),
                name=name.strip(),
                password_hash=hash_password(password),
                role="admin",
            )
        )
        db.session.commit()
        click.echo("Administrator created.")

    if not WORDNET_READY:
        app.logger.warning(
            "WordNet unavailable: tokenizer-only mode. Run python train_model.py --download-nltk before starting for lemmatization."
        )
    if os.environ.get("TESSERACT_CMD"):
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        use_reloader=False,
        threaded=True,
    )
