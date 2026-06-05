FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=cms_platform.settings \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        libxml2-dev \
        libxslt1-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/staticfiles /app/media \
    && DJANGO_SECRET_KEY=build-only DATABASE_URL=sqlite:////tmp/build.sqlite3 \
       python manage.py collectstatic --noinput

RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
# --timeout 180: the AI annotation endpoint calls OpenAI synchronously and can
# run well past 60s on large pages. At 60s Gunicorn killed the worker mid-request
# and the proxy served an HTML 502, which the dashboard fetch tried to JSON.parse
# ("Unexpected token '<'"). The OpenAI client timeout (settings.OPENAI_TIMEOUT,
# default 120s) is set BELOW this so a hung API returns a clean JSON error first.
# gthread workers: the AI annotation endpoint blocks on a synchronous OpenAI
# call for up to ~120s. With sync workers, 3 concurrent annotations would occupy
# all 3 workers and freeze the entire dashboard for everyone. Threads let each
# worker keep serving other requests while one thread is parked on OpenAI.
# --timeout 180 is the hard worker budget; OPENAI_TIMEOUT (120s) fires first so a
# hung API returns a clean JSON error instead of a killed worker (HTML 502).
CMD ["gunicorn", "cms_platform.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--worker-class", "gthread", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-"]
