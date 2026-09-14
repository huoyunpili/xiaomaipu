FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
ARG APP_VERSION=development
LABEL org.opencontainers.image.title="Xianyu Seller Local"
LABEL org.opencontainers.image.version="${APP_VERSION}"
RUN pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple uv==0.12.10
WORKDIR /srv/app
ENV UV_COMPILE_BYTECODE=1 PATH="/srv/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY manage.py ./
RUN DJANGO_SETTINGS_MODULE=app.config.settings.development python manage.py collectstatic --noinput
RUN useradd --uid 10001 --create-home seller
RUN mkdir -p /srv/private-media /srv/backups && chown seller:seller /srv/private-media /srv/backups
USER seller
EXPOSE 8000
CMD ["gunicorn", "app.config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "300"]
