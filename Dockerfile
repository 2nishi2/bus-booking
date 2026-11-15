FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY schema.sql .
COPY initdb.py .
RUN useradd --system --uid 10001 --create-home appuser
ARG APP_VERSION=local
ENV APP_VERSION=${APP_VERSION}
ENV HOME=/tmp
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u,sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["sh", "-c", "python initdb.py && exec gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 app:app"]