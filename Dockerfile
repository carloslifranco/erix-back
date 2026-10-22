FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libxcb1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ocr.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements-ocr.txt

RUN python -c "import cv2; from paddleocr import PaddleOCR"

COPY app ./app
COPY streamlit_app.py .
COPY scripts ./scripts
RUN mkdir -p /data/uploads /app/models

EXPOSE 8000 8501

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
