# TrueHire

A job fraud detection application that checks job descriptions, screenshots, and URLs.

**Built with:** Flask, SQLite, Scikit-learn, Tesseract OCR, HTML, CSS, and JavaScript.

## Features

- Text, image, and URL scanning
- Scan history and user feedback
- Admin dashboard and automatic model retraining

## Run locally (Windows)

Requires Python 3.11. Install Tesseract separately for screenshot scanning.

```powershell
git clone https://github.com/Varshini1928/Truehire.git
cd Truehire
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe train_model.py --download-nltk
.\.venv\Scripts\python.exe app.py
```

Open http://localhost:5000 and create an account. Keep the terminal open.

## Admin access

In another terminal, open the project folder and run:

```powershell
.\.venv\Scripts\python.exe manage.py create-admin
```

Use a new email and a password of at least 12 characters. Sign in with that account and open http://localhost:5000/admin.

The initial model uses synthetic data; real-world accuracy has not been validated.

## Deploy on Render

Use the repository’s `render.yaml` with the Docker runtime. Set your admin email when prompted; the admin password is generated in Render’s environment variables.

The free plan loses SQLite accounts, history, feedback, and model updates when the service restarts or sleeps.
