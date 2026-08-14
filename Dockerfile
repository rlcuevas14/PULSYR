FROM python:3.12-slim

WORKDIR /app

# Locked runtime deps first: reproducible image + a docker layer that only
# invalidates when requirements.lock changes.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
