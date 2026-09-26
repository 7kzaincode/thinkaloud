# thinkaloud processor: session folder -> trajectory.json
#   docker build -t thinkaloud-processor processor
#   docker run --rm -v "$PWD/sessions:/data" -v thinkaloud-models:/models thinkaloud-processor /data
# Optional OCR (emails visible in screenshots): --build-arg WITH_OCR=1, then pass --ocr
FROM python:3.12-slim

ARG WITH_OCR=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models

RUN if [ "$WITH_OCR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends tesseract-ocr=5.* \
      && rm -rf /var/lib/apt/lists/*; fi

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_OCR" = "1" ]; then pip install --no-cache-dir pillow==12.3.0 pytesseract==0.3.13; fi

COPY thinkaloud ./thinkaloud
RUN useradd -m app && mkdir -p /models && chown app /models
USER app
VOLUME ["/models"]
ENTRYPOINT ["python", "-m", "thinkaloud"]
