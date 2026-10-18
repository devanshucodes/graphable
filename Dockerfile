# Backend image: API, MCP server, bootstrap (one image, several entrypoints)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
RUN pip install "uv==0.12.10" && uv pip install --system -r pyproject.toml
COPY graphable ./graphable
COPY datagen ./datagen
COPY evals_out ./evals_out
EXPOSE 8000 8765
CMD ["uvicorn", "graphable.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
