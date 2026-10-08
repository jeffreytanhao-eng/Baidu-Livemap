# 可选附录镜像（非验收项；本地开发请用 venv，见 README）
FROM python:3.12-slim

WORKDIR /app
COPY packages/geo /app/packages/geo
COPY apps/api/requirements.txt /app/apps/api/requirements.txt
RUN pip install --no-cache-dir -r apps/api/requirements.txt \
    && pip install --no-cache-dir -e packages/geo

COPY apps/api /app/apps/api
COPY data /app/data

ENV CACHE_DIR=/app/.cache \
    FABRIC_ROOT=/app/data/map_fabric \
    SAMPLES_DIR=/app/data/samples

WORKDIR /app/apps/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
