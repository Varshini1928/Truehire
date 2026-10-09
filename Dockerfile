FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NLTK_DATA=/usr/local/share/nltk_data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m nltk.downloader -d /usr/local/share/nltk_data wordnet
COPY . .
RUN python train_model.py
RUN useradd --create-home --uid 10001 truehire && mkdir -p /app/instance && chown -R truehire:truehire /app
USER truehire
EXPOSE 5000
# One process is deliberate: scheduler and hot-swap registry are process-local.
CMD ["python", "start_server.py"]
