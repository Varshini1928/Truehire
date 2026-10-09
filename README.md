# TrueHire

A complete Flask job-fraud screening application with a dark glass dashboard,
text + screenshot + URL analysis, private scan history, admin controls, and
background retraining. The application runs locally or in the included Docker
container. The source repository is https://github.com/Varshini1928/Truehire.
This repository contains source code; hosting is configured separately.

## See the interface first

- `templates/dashboard.html` — user workspace: scanner, live logs, confidence,
  charts, risk gauge, history, corrections.
- `templates/admin.html` — platform analytics, resource health, model metrics,
  retraining, feedback, and member roles.
- `templates/auth.html` — registration and sign-in.
- `static/css/app.css` — responsive dark glass design, cyan/emerald/crimson
  palette, keyboard focus, skeletons, scan sweep, reduced-motion support.
- `previews/` — screenshots taken from the running application with sample scans.

The dashboard begins with empty, real account data. Screenshot sample data is
not inserted into new accounts.

## Quick start: Docker (Windows, macOS, Linux)

Install Docker Desktop / Docker Engine, open a terminal in this folder, then:

```bash
docker compose up --build -d
```

Open **http://localhost:5000** and create an account. Docker installs Tesseract,
WordNet and Python dependencies; the first start trains the bootstrap model and
creates SQLite automatically. The initial build needs internet access.

Create an administrator interactively:

```bash
docker compose exec truehire python manage.py create-admin
```

Choose a new email and a password with at least 12 characters. Sign in with that
account to open **http://localhost:5000/admin**. There are no default passwords.
Management commands do not start another scheduler.

Stop the app with `docker compose down`. The named volume retains accounts,
scans, model and the generated signing key. Do not use `down -v` unless you intend
to permanently delete that data.

## Local setup: Windows PowerShell

Use **Python 3.12** (or your installed **Python 3.11**). Extract the ZIP and enter the `TrueHire` folder.
If using Python 3.11, replace `py -3.12` with `py -3.11` in the first command below.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe train_model.py --download-nltk
.\.venv\Scripts\python.exe manage.py create-admin
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. No PowerShell activation policy change is needed.

For screenshot OCR, install the **Tesseract executable** separately, including
English language data. See the Windows install links in the official Tesseract
[installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html).
Set its actual location in `.env`, for example:

```dotenv
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Installing `pytesseract` alone does not install the OCR executable. Docker avoids
this separate installation step.

## Local setup: Ubuntu / macOS

Install Python 3.12 and Tesseract (Ubuntu: `sudo apt-get install tesseract-ocr
 tesseract-ocr-eng`; macOS: `brew install tesseract`). Then:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python train_model.py --download-nltk
.venv/bin/python manage.py create-admin
.venv/bin/python app.py
```

If `python train_model.py` is run without WordNet installed, a tokenizer-only
fallback works offline and reports that limitation in the startup log. Install
WordNet with `--download-nltk` and retrain before enabling full lemmatization.
Model preprocessing mode is stored and checked so inference cannot silently
switch to a different text representation.

## Typical flow

1. Sign in or register.
2. Paste a job description, choose a screenshot, and/or enter a job URL.
3. Screenshots trigger OCR immediately. Review/edit the extracted text.
4. Click **Analyze opportunity**. All populated tabs are included.
5. Review the assessment, reasons, and score. History is saved per account.
6. Open **Report incorrect prediction**, choose a corrected label, and submit.
7. Administrators can inspect global analytics, manage users, or force retraining.

Scan logs reflect actual worker stages. No synthetic progress timer pretends
that a network or model operation has finished. Image upload has a separate
progress indicator and scan submission waits until OCR completes.

## Detection and confidence

### Text

Unicode normalization → NLTK regex tokenization → WordNet lemmatization →
TF-IDF word unigrams/bigrams → balanced Logistic Regression. Negation words
are preserved. Safety rules flag fee requests, credentials requests, and
"no interview required". A short negation window handles examples such as
"do not pay registration fee"; nuanced or quoted language can still cause
false positives.

Text and screenshot model probabilities are weighted 60/40 when both exist.
Weights renormalize if only one text source exists. A completed URL inspection
contributes 20% of combined risk; available text contributes 80%. Missing
sources do not count as safe evidence. Scores between 40% and 60% fraud risk
produce **needs review**. Unsupported/insufficient text also produces review.
A network inspection failure forces review unless an independent safety rule
already establishes a high-risk flag.

Displayed confidence is `max(combined_risk, 1-combined_risk) * 100`, a model-derived
score, **not a calibrated probability that a job is genuine**. The API also
returns the separate `risk_score`. A deterministic policy flag has no model
confidence (`null`), and the UI identifies it as rule-based instead of inventing
100% statistical certainty.

### URLs and redirects

`url_checker.py` uses `requests.Session.get(..., allow_redirects=True)`.
`GuardedSession.send()` checks **every** destination before the transport connects.
Same-host redirects are followed, up to five hops. Any host change immediately
flags and stops the chain, even if a later redirect would return to the origin.
The final host is also compared with the original.

- Raw IP URLs, suspicious `.xyz` / `.top` / `.tk` endings, local domains,
  credentials in URLs, nonstandard ports, and private DNS answers are blocked.
- HTTPS-to-HTTP redirect downgrades are blocked.
- All DNS answers must be public. The transport pins a vetted IP and retains
  the original TLS SNI/certificate hostname and Host header to prevent DNS
  rebinding. It does not disable certificate verification or use environment
  proxy credentials.
- Redirect bodies are closed immediately; page HTML is never parsed or run.
- Connect/read timeouts, a chain time budget, and queue limits bound work.
  System DNS lookup duration still depends on the operating system resolver.
- A trusted-domain suffix requires a dot boundary. `linkedin.com.evil.example`
  is never treated as LinkedIn.
- Unknown domains are not automatically fraudulent. Unknown **cross-domain
  redirects** are blocked, as requested. A URL-only trusted-domain result still
  needs review because it does not validate an individual posting.

**Strict policy tradeoff:** `example.com` → `www.example.com`, URL shorteners,
tracking services and legitimate external application systems can be flagged.
The reasons explain this. Suspicious TLDs are risk heuristics, not proof of fraud.
This build includes a local trusted list, not a live commercial reputation feed.

See the official [Requests redirect API](https://requests.readthedocs.io/en/stable/api/).

## Automated learning

`APScheduler` runs inside the one application process. A scheduled job runs
every **24 hours**; startup derives the next run from the model timestamp.
A 30-second poll and each feedback submission check whether **50 new feedback
entries** are pending. The administrator button can enqueue a run immediately.
Requests and scans continue while training runs in the background.

Each run:

1. Reads original training CSV plus surviving feedback and related scan text.
2. Excludes exact normalized benchmark text; deduplicates feedback; caps each
   account at 100 eligible examples per run. For conflicting duplicate text,
   the newest correction wins.
3. Trains a fresh pipeline without mutating the current estimator.
4. Evaluates against the fixed synthetic benchmark. An F1 fall greater than
   0.02, or F1 below 0.80, rejects promotion and preserves the old model.
5. Writes the candidate to a temporary file, atomically replaces the local
   artifact, and swaps the in-memory model under a lock.
6. Marks incorporated feedback as trained. IDs stored in the model let startup
   repair this bookkeeping if the process stopped between steps 5 and 6.

Skipped empty, benchmark-contaminating, or over-cap feedback remains pending.
A failed threshold run is not automatically retried in a tight loop; a new
feedback ID, daily schedule, or admin action permits another attempt. Feedback
updates language classification only: it cannot disable hard URL/safety rules.
A user deletion removes their raw scans/corrections immediately; retrain to
remove those examples from the model artifact as well.

Source: [APScheduler 3.x user guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html).

## Accuracy and latency: measured, not promised

The bootstrap data contains **320 synthetic training examples** and **96
held-out synthetic examples** using different sentence templates. The corpus
is intentionally simple, small, balanced, and English-only. It is not a
representative fraud benchmark; high synthetic scores do not establish
real-world accuracy. **94% is a target, not an achieved production claim.**

Text inference target: **under 200 ms**. OCR target: **under 1.5 seconds**.
Admin shows actual recent p95 measurements; they may exceed these targets.
Network inspection and queue wait are separate from text inference. OCR is
bounded to 5 MB, 12 megapixels, and a 10-second Tesseract timeout. Screenshot
quality, hardware, concurrency, DNS and remote servers affect latency.

`VALIDATION.md` records the tests actually run for this delivery. Production
acceptance needs a licensed representative labeled dataset, a leakage-resistant
split (for example, employer/time split), calibration, abuse testing and load
testing on the intended hosting environment.

## Security and deployment scope

- PBKDF2-SHA256, 600,000 iterations and random salts.
- Two-hour HS256 JWTs in HttpOnly, SameSite=Strict cookies.
- Token-bound CSRF header for cookie-authenticated changes, origin checking,
  JSON content enforcement, revocation on logout and role changes.
- Roles are checked against the database for every authenticated request.
- Rate limits for login, registration, OCR, scans, feedback and retraining.
- Per-user authorization on histories, task status and corrections.
- Escaped Jinja output and safe DOM insertion for user-supplied content.
- Byte/type/pixel-limited uploads; image bytes are processed in memory.
- Bounded four-thread task pool; completed job payloads expire after 24 hours.
- Audit rows record admin role changes, deletions and retrain requests.
- SQLite foreign keys, WAL, and a write timeout are enabled.
- Secrets and personal database files are excluded from the ZIP and version control.

For an HTTPS deployment, set `COOKIE_SECURE=true`, provide a random `SECRET_KEY`
via your host's secret manager, and use TLS termination in front of Gunicorn.
Set a persistent `INSTANCE_DIR` so SQLite, signing key and model survive restarts.
Back up that directory consistently. Do not expose Flask's development server.

**Use exactly one Gunicorn worker**, with threads as in the Dockerfile. This is
a self-contained single-instance implementation, not a distributed enterprise
service. Scaling requires a shared queue (e.g. Celery), external database,
shared rate limiting, scheduler leadership and model registry/version reload.
An interrupted in-process task is marked failed after restart and must be resubmitted.

Tailwind is loaded from CDN as requested; the main appearance also lives in
local CSS. The standard Alpine build requires CSP eval allowances. Compile and
self-host Tailwind and use Alpine's CSP build for a stricter production CSP.
Inter fonts, Chart.js, Alpine.js and AOS are bundled locally so the main interface and charts work without their CDNs. Third-party licenses are in `static/vendor/`.

Automated user labels are not ground truth. Deduplication and a synthetic
promotion gate do not eliminate model poisoning. Human-reviewed feedback and
independent model validation are needed for serious production use. Email
verification, password recovery, enterprise SSO, distributed operation and
formal migrations are not included in this requested single-instance scope.

## Architecture

| File | Responsibility |
|---|---|
| `app.py` | Flask factory, REST routes, task pool, aggregation, management command |
| `models.py` | Users, scan history, feedback, jobs, audit records |
| `auth.py` | Password hashing, JWT, CSRF, RBAC |
| `ml_engine.py` | Text preprocessing, model inference, atomic model replacement, OCR |
| `url_checker.py` | URL policy, redirect inspection, DNS-pinned transport |
| `scheduler.py` | Daily and threshold retraining, promotion gate, retention |
| `train_model.py` | Synthetic dataset generation and initial model training |
| `manage.py` | Admin creation without starting workers or scheduler |
| `templates/` | User dashboard, admin dashboard, auth, shared layout |
| `static/js/` | Real scans, polling, charts, feedback, authentication, administration |
| `data/` | Synthetic training and benchmark CSVs |
| `instance/` | Runtime SQLite DB, signing secret and model; generated automatically |
| `tests/` | Backend, security, redirects, OCR, model lifecycle integration tests |

## REST endpoints

| Method | Endpoint | Access / purpose |
|---|---|---|
| POST | `/api/auth/register`, `/api/auth/login` | Create account / issue secure cookie session |
| POST | `/api/auth/logout` | Authenticated; revoke session tokens |
| GET | `/api/me` | Current user |
| POST | `/api/ocr` | Multipart `image`; returns asynchronous job ID |
| POST | `/api/scans` | JSON `text`, `image_text`, `url`, optional `ocr_job_id` |
| GET | `/api/jobs/<uuid>` | Owner-only live status, logs, result |
| GET | `/api/scans` | Owner history: `page`, `q`, `verdict` |
| GET | `/api/scans/<id>` | Owner-only full result |
| POST | `/api/scans/<id>/feedback` | One correction per owned scan: `{"label":"real"}` / `fake` |
| GET | `/api/stats` | Personal chart data |
| GET | `/api/admin/stats` | Global metrics and system/model health |
| POST | `/api/admin/retrain` | Enqueue background retraining |
| GET | `/api/admin/users` | Paginated members: `page`, `q` |
| PATCH | `/api/admin/users/<id>` | Set role with `{"role":"admin"}` / `user` |
| DELETE | `/api/admin/users/<id>` | Delete member and dependent records |
| GET | `/health` | Process/model health check |

For cookie-authenticated mutations, send the `csrf_token` cookie value in
`X-CSRF-Token`. POST/PATCH bodies must be JSON except OCR uploads. Background
endpoints return HTTP 202; poll the returned ID until `completed` or `failed`.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Tests use temporary databases. The real OCR test requires Tesseract; otherwise
it is explicitly skipped. Network-policy tests use mocked HTTP transports and
DNS responses so they are repeatable and never contact third-party job sites.
