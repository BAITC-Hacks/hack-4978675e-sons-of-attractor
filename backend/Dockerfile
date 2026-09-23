FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0

WORKDIR /service

COPY backend/requirements.txt /service/backend/requirements.txt
COPY backend/constraints.txt /service/backend/constraints.txt

RUN pip install --no-cache-dir -r /service/backend/requirements.txt

COPY backend /service/backend
COPY frontend/index.html frontend/styles.css frontend/app.js /service/frontend/
COPY frontend/lib /service/frontend/lib

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
