# ---- web client build -------------------------------------------------------
FROM node:22-slim AS webbuild
WORKDIR /src
COPY web ./web
RUN cd web && (npm ci || npm install) && npm run build

# ---- application ------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIDCRAFT_HOME=/data

# LibreOffice (PDF field/TOC refresh) + CJK fonts + optional scanned-PDF OCR.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer python3-uno fonts-noto-cjk \
        poppler-utils tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY bidcraft ./bidcraft
COPY --from=webbuild /src/bidcraft/web_dist ./bidcraft/web_dist
RUN pip install --no-cache-dir ".[api]"

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["bidcraft"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
