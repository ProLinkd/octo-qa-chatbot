#!/bin/bash
set -eu
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    --workers "${WEB_CONCURRENCY:-1}" \
    --timeout 400 \
    --graceful-timeout 400 \
    --bind 0.0.0.0:8501
