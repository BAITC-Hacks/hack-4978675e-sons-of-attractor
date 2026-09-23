FROM python:3.13-slim

WORKDIR /service

COPY backend/requirements.txt /service/backend/requirements.txt

RUN pip install --no-cache-dir -r /service/backend/requirements.txt

COPY . /service

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
