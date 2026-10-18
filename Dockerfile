# Backend image: API, MCP server, bootstrap (one image, several entrypoints)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# Install the exact versions pinned in uv.lock (runtime dependencies only, no dev extras)
COPY pyproject.toml uv.lock ./
RUN pip install "uv==0.12.10" \
    && uv export --frozen --no-dev --no-hashes --no-emit-project --quiet -o /tmp/req.txt \
    && uv pip install --system -r /tmp/req.txt
COPY graphable ./graphable
# datagen/out (the committed synthetic dataset) is included, so bootstrap loads it instead of regenerating it
COPY datagen ./datagen
COPY evals_out ./evals_out
EXPOSE 8000 8765
CMD ["uvicorn", "graphable.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
