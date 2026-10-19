FROM node:22-alpine AS frontend
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 NEWS_BRIEF_DATA_DIR=/app/data NEWS_BRIEF_FRONTEND_DIR=/app/frontend/dist PIP_NO_CACHE_DIR=1
COPY pyproject.toml requirements.lock ./
COPY src/ ./src/
RUN pip install -c requirements.lock . && \
    useradd --uid 10001 --create-home newsbrief && \
    mkdir -p /app/data && chown newsbrief:newsbrief /app/data
COPY --from=frontend /ui/dist/ ./frontend/dist/
USER newsbrief
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["newsbrief", "serve", "--host", "0.0.0.0", "--port", "8000"]

