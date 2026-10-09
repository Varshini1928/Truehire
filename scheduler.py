"""24-hour / 50-feedback auto-retraining with atomic model promotion.

Deployment contract: ONE application process, threaded request handling.
The app owns one APScheduler and one model object. Do not use multiple Gunicorn
workers: their in-memory models and threshold counters would diverge. Move jobs
to a dedicated queue/model registry before horizontal scaling.
"""

import atexit
import threading
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select, func
from models import db, Feedback, ScanHistory, Job, utcnow
from ml_engine import train_bundle, text_hash
from train_model import read_rows


class Retrainer:
    def __init__(self, app, engine):
        self.app = app
        self.engine = engine
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.status = "idle"
        self.error = None
        self.last_threshold_id = 0
        self.scheduler = BackgroundScheduler(
            timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1}
        )
        trained = datetime.fromisoformat(engine.metadata()["trained_at"])
        first = max(
            datetime.now(timezone.utc) + timedelta(seconds=10),
            trained + timedelta(hours=24),
        )
        self.scheduler.add_job(
            self.run, "interval", hours=24, id="daily-retrain", next_run_time=first
        )
        self.scheduler.add_job(
            self.check_threshold, "interval", seconds=30, id="feedback-threshold"
        )
        self.scheduler.add_job(self.cleanup, "interval", hours=1, id="job-retention")

    def start(self):
        self.scheduler.start()
        atexit.register(self.shutdown)

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def info(self):
        job = self.scheduler.get_job("daily-retrain")
        next_run = getattr(job, "next_run_time", None) if job else None
        with self.state_lock:
            return {
                "status": self.status,
                "error": self.error,
                "next_run": next_run.isoformat() if next_run else None,
            }

    def check_threshold(self):
        with self.app.app_context():
            count = db.session.scalar(
                select(func.count(Feedback.id)).where(Feedback.trained.is_(False))
            )
            newest = db.session.scalar(select(func.max(Feedback.id))) or 0
            if (
                count >= self.app.config["FEEDBACK_THRESHOLD"]
                and newest > self.last_threshold_id
            ):
                self.last_threshold_id = newest
                self.request()

    def request(self):
        with self.state_lock:
            if self.status in ("queued", "running"):
                return False
            self.status = "queued"
            self.error = None
        # Fixed job id prevents overlapping button/threshold triggers.
        self.scheduler.add_job(
            self.run, "date", id="requested-retrain", replace_existing=True
        )
        return True

    def run(self):
        if not self.lock.acquire(blocking=False):
            return
        with self.state_lock:
            self.status = "running"
            self.error = None
        try:
            with self.app.app_context():
                base = read_rows(self.app.config["DATA_DIR"] / "train.csv")
                benchmark = read_rows(self.app.config["DATA_DIR"] / "benchmark.csv")
                forbidden = {text_hash(row["text"]) for row in benchmark}
                # Every pass starts with ORIGINAL data plus ALL surviving feedback,
                # not only the newest batch, so earlier corrections are retained.
                rows = db.session.execute(
                    select(Feedback, ScanHistory.text)
                    .join(ScanHistory, Feedback.scan_id == ScanHistory.id)
                    .order_by(Feedback.id)
                ).all()
                feedback_ids = []
                dedup = {}
                per_user = {}
                for feedback, text in rows:
                    key = text_hash(text)
                    if not text.strip() or key in forbidden:
                        continue
                    if per_user.get(feedback.user_id, 0) >= 100:
                        continue
                    per_user[feedback.user_id] = per_user.get(feedback.user_id, 0) + 1
                    dedup[key] = {"text": text, "label": int(feedback.label == "fake")}
                    feedback_ids.append(feedback.id)
                # Corrections are bounded and deduplicated; this limits but does not
                # eliminate poisoning risk. A human-reviewed corpus is required
                # before using automatic feedback in a high-stakes production model.
                training = base + list(dedup.values())
                candidate = train_bundle(training, benchmark, feedback_ids)
                current = self.engine.metadata()
                if candidate["metrics"]["f1"] < max(
                    0.80, current["metrics"]["f1"] - 0.02
                ):
                    raise ValueError(
                        "Candidate rejected: synthetic benchmark F1 regressed. Previous model retained."
                    )
                self.engine.swap(candidate)
                # IDs in the artifact make this step crash-recoverable at startup.
                if feedback_ids:
                    db.session.execute(
                        db.update(Feedback)
                        .where(Feedback.id.in_(feedback_ids))
                        .values(trained=True)
                    )
                    db.session.commit()
            with self.state_lock:
                self.status = "completed"
        except Exception as exc:
            self.app.logger.exception("Model retraining failed")
            with self.state_lock:
                self.status = "failed"
                self.error = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Retraining failed. Check server logs; previous model remains available unless already promoted."
                )
        finally:
            self.lock.release()

    def cleanup(self):
        with self.app.app_context():
            db.session.execute(
                db.delete(Job).where(
                    Job.created_at < utcnow() - timedelta(hours=24),
                    Job.status.in_(["completed", "failed"]),
                )
            )
            db.session.commit()
