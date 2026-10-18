FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Browser/system dependencies are cached independently from application changes.
ARG PLAYWRIGHT_VERSION=1.62.0
RUN pip install --no-cache-dir playwright==${PLAYWRIGHT_VERSION} \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && useradd --uid 10001 --create-home domaincheck \
    && mkdir -p /var/lib/domaincheck/artifacts \
    && chown -R domaincheck:domaincheck /var/lib/domaincheck
WORKDIR /app/backend
COPY pyproject.toml ./
RUN python -c "import subprocess,sys,tomllib; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']])"
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
USER domaincheck
CMD ["python", "-m", "uvicorn", "domaincheck.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
