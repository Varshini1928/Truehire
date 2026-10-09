# Delivery validation

Verified on 9 October 2026 using Python 3.12 and Chromium 140.

## Automated integration suite

**29 tests passed, zero skipped.** Coverage includes:

- PBKDF2 password storage; authentication, logout revocation, CSRF, origin rejection.
- Private scan/feedback ownership and RBAC.
- Real/fraud/uncertain text outcomes and combined text + screenshot channels.
- Rule overrides bypassing the classifier, including suspicious URL endings.
- Private DNS answers, raw IPs, malicious domain lookalikes.
- Same-domain redirects, cross-domain blocking before transport, redirect loops.
- Network failures producing review instead of a safe verdict.
- Real Tesseract OCR and rejection of corrupt images.
- Feedback persistence, duplicate correction rejection and threshold trigger.
- Actual APScheduler background training, 24-hour configuration and hot swap.
- Admin role changes, token revocation and dependent-record deletion.

The mocked network tests establish policy behavior; they are not a live
reputation audit of LinkedIn, Indeed, or any third-party site.

## Browser validation

Full browser flow tested against a real running Flask app and temporary SQLite DB:
registration → scanner → both genuine and fraudulent sample descriptions →
feedback → screenshot upload → Tesseract OCR preview → history/charts → logout
→ admin login → force retrain → model version changed → promote/demote member.

- Zero browser JavaScript errors.
- Charts, Alpine and AOS successfully loaded from bundled assets.
- Desktop: 1512 × 1100 viewport.
- Mobile: 390 × 844 viewport; user/admin layouts had no page-level horizontal overflow.
- User, admin and mobile screenshots visually inspected.
- Reproducible browser harness: `tests/smoke_browser.py`.

## Observed timings and benchmark

On the small browser test inputs in this environment:

- Text inference sample: approximately **0.90 ms**.
- Screenshot OCR sample: approximately **142 ms**.

These are small-sample observations, **not load-test p95 guarantees**. The dashboard
computes p95 from available observations, which may initially be only one sample.
No production hardware/load/network SLO has been established.

The 96-example synthetic benchmark returned precision/recall/F1/accuracy of
1.00. This reflects a deliberately easy synthetic corpus, not evidence of
real-world 100% or 94% accuracy. Real-world validation remains required.

## Not verified

- Docker image build and Docker volume behavior (Docker runtime not exercised).
- Windows-specific Tesseract installation on the user's laptop.
- Production deployment, HTTPS proxy configuration or high-concurrency operation.
- Independent real-world fraud accuracy, probability calibration or legal review.

The runnable source, Docker configuration, requirements, setup steps and
single-instance operational constraints are included. This is a tested
production-oriented foundation, not a certified enterprise deployment.
