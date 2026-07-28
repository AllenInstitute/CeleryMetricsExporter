# Pinned to the Python series running in production (3.9.x). uv.lock is the
# source of truth for what gets installed (mirrors materializationengine).
FROM python:3.9-slim-bookworm

# uv is the installer.
RUN pip install --no-cache-dir uv

# uv build settings (same as materializationengine).
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install ONLY the locked dependencies first, so this layer caches across code
# changes. --frozen makes the build fail if uv.lock is out of date with
# pyproject.toml, guaranteeing the image matches the lockfile.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Run everything through the virtualenv uv created.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Application source.
COPY . /app


EXPOSE 9540

ENTRYPOINT ["python", "/app/cli.py"]
