"""Gunicorn entry point for Docker and Render, using the host-provided PORT."""

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535.")
    # Replace this process so platform termination signals reach Gunicorn.
    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "--bind",
            f"0.0.0.0:{port}",
            "--workers",
            "1",
            "--threads",
            "4",
            "--timeout",
            "120",
            "--access-logfile",
            "-",
            "app:create_app()",
        ],
    )
