FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev postgresql-client tesseract-ocr && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 ledgergate && useradd --system --uid 10001 --gid ledgergate --home-dir /app --no-create-home ledgergate
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/uploads && chown 10001:10001 /app/uploads && chmod 0555 ./scripts/bootstrap_runtime_role.sh
EXPOSE 8000
USER 10001:10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
