FROM python:3.12-slim

WORKDIR /app

# Refresh fixed Debian packages from the image's configured release before
# installing application dependencies.  The supply-chain gate rejects known
# fixable HIGH/CRITICAL findings in an otherwise current slim base image.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Locked runtime deps first: reproducible image + a docker layer that only
# invalidates when requirements.lock changes.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
