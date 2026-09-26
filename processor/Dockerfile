# thinkaloud processor: recording folders -> trajectory.json + playback.mp4 (+ exports)
#
# One recording:   docker run --rm -v "$PWD/sessions:/data" -v thinkaloud-models:/models thinkaloud-processor /data/<id>
# Batch (bounded concurrency, persistent job status, retries):
#   docker run --rm -v "$PWD/sessions:/data/sessions" -v "$PWD/jobs:/data/jobs" -v thinkaloud-models:/models \
#     thinkaloud-processor batch /data/sessions --jobs /data/jobs --concurrency 2
# or: docker compose run --rm processor   (see docker-compose.yml at the repo root)
#
# This container only post-processes recordings. Capture (the recorder) runs natively on Windows.
# Optional OCR (emails visible in screenshots): --build-arg WITH_OCR=1, then pass --ocr
FROM python:3.12-slim

ARG WITH_OCR=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models THINKALOUD_RUNNER=docker

RUN if [ "$WITH_OCR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends tesseract-ocr=5.* \
      && rm -rf /var/lib/apt/lists/*; fi

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_OCR" = "1" ]; then pip install --no-cache-dir pytesseract==0.3.13; fi

COPY thinkaloud ./thinkaloud
RUN useradd -m app && mkdir -p /models && chown app /models
USER app
VOLUME ["/models"]
ENTRYPOINT ["python", "-m", "thinkaloud"]
