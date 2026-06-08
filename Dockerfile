FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src
ENTRYPOINT ["planka-checker"]
CMD ["daily", "--summarize"]
