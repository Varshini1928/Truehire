"""Local text model and bounded OCR. Never download NLP resources at request time."""

import hashlib
import io
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
import joblib
import nltk
import numpy as np
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError
from nltk.stem import WordNetLemmatizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support, accuracy_score
from sklearn.pipeline import Pipeline

Image.MAX_IMAGE_PIXELS = 12_000_000
LEMMATIZER = WordNetLemmatizer()
# Startup warm-up avoids NLTK's lazy corpus loader racing in worker threads.
try:
    LEMMATIZER.lemmatize("jobs")
    WORDNET_READY = True
except LookupError:
    WORDNET_READY = False

SAFETY_PATTERNS = [
    (
        r"\bpay\s+(?:a\s+|the\s+)?(?:registration|processing|security|training)\s+fee\b",
        "Upfront fee requested.",
    ),
    (
        r"\bno\s+interview\s+(?:is\s+)?required\b",
        "Hiring without an interview promised.",
    ),
    (
        r"\b(?:send|pay|transfer)\s+(?:us\s+)?(?:a\s+)?(?:deposit|money|bitcoin|crypto)\b",
        "Money transfer requested.",
    ),
    (
        r"\b(?:share|send)\s+(?:your\s+)?(?:otp|bank password)\b",
        "Sensitive credentials requested.",
    ),
]


def clean_text(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"https?://\S+|\b\S+@\S+\b", " ", text)
    tokens = nltk.tokenize.regexp_tokenize(text, r"[a-z]{2,}")
    # Keep negation (no/not/never). Removing it changes safety-related meaning.
    if WORDNET_READY:
        tokens = [LEMMATIZER.lemmatize(word) for word in tokens]
    return " ".join(tokens)


def safety_signals(text):
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower())
    reasons = []
    for pattern, reason in SAFETY_PATTERNS:
        for match in re.finditer(pattern, normalized):
            # A small negation window avoids flagging "do not pay registration fee".
            # This is a conservative heuristic, not a full semantic parser.
            prefix = normalized[max(0, match.start() - 45) : match.start()]
            if not re.search(
                r"\b(?:not|never|don[’\x27]t|avoid|warn\w*)\b[^.!?]{0,35}$", prefix
            ):
                reasons.append(reason)
                break
    return reasons


def text_hash(text):
    return hashlib.sha256(clean_text(text).encode()).hexdigest()


def pipeline():
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2), max_features=25000, sublinear_tf=True
                ),
            ),
            (
                "lr",
                LogisticRegression(
                    max_iter=1200, class_weight="balanced", random_state=42
                ),
            ),
        ]
    )


def train_bundle(train_rows, benchmark_rows, feedback_ids=()):
    texts = [clean_text(r["text"]) for r in train_rows]
    labels = [int(r["label"]) for r in train_rows]
    estimator = pipeline().fit(texts, labels)
    expected = [int(r["label"]) for r in benchmark_rows]
    predicted = estimator.predict([clean_text(r["text"]) for r in benchmark_rows])
    precision, recall, f1, _ = precision_recall_fscore_support(
        expected, predicted, average="binary", zero_division=0
    )
    metrics = dict(
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        accuracy=float(accuracy_score(expected, predicted)),
    )
    return {
        "pipeline": estimator,
        "metrics": metrics,
        "version": uuid.uuid4().hex[:12],
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_size": len(train_rows),
        "benchmark_size": len(benchmark_rows),
        "feedback_ids": list(feedback_ids),
        "preprocessor": "wordnet" if WORDNET_READY else "regex-only",
        "benchmark_kind": "synthetic-held-out",
    }


class ModelEngine:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._bundle = joblib.load(
            self.path
        )  # Only load trusted locally produced artifacts.
        expected = "wordnet" if WORDNET_READY else "regex-only"
        if self._bundle["preprocessor"] != expected:
            raise RuntimeError(
                "NLP resources changed. Run python train_model.py again."
            )
        self.predict("Software engineer requirements and interview process")

    def metadata(self):
        with self._lock:
            return {k: v for k, v in self._bundle.items() if k != "pipeline"}

    def predict(self, text):
        start = time.perf_counter()
        cleaned = clean_text(text)
        with self._lock:
            bundle = (
                self._bundle
            )  # Snapshot: concurrent scans finish using one model version.
        vector = bundle["pipeline"]["tfidf"].transform([cleaned])
        if vector.nnz == 0 or len(cleaned.split()) < 4:
            return {
                "probability": None,
                "version": bundle["version"],
                "duration_ms": (time.perf_counter() - start) * 1000,
            }
        probability = float(bundle["pipeline"]["lr"].predict_proba(vector)[0, 1])
        return {
            "probability": probability,
            "version": bundle["version"],
            "duration_ms": (time.perf_counter() - start) * 1000,
        }

    def swap(self, bundle):
        # Write a complete file before replacing; a failed write leaves the old model intact.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            joblib.dump(bundle, temp)
            with self._lock:
                os.replace(temp, self.path)
                self._bundle = bundle
        finally:
            temp.unlink(missing_ok=True)


def extract_image(data):
    start = time.perf_counter()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("Image must be at most 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("Only PNG, JPG, and WEBP images are supported.")
            if image.width * image.height > 12_000_000:
                raise ValueError(
                    "Image exceeds 12 megapixels. Crop the job description first."
                )
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2200, 2200))
            image = ImageOps.autocontrast(ImageOps.grayscale(image))
            text = pytesseract.image_to_string(
                image, lang="eng", config="--psm 6", timeout=10
            ).strip()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("This image could not be decoded safely.") from exc
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError(
            "Tesseract is not installed. Follow the OCR setup in README.md."
        ) from exc
    except RuntimeError as exc:
        raise ValueError("OCR timed out. Try a smaller or clearer screenshot.") from exc
    if not text:
        raise ValueError("No readable English text found. Upload a clearer screenshot.")
    return {
        "text": text[:20000],
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
    }
