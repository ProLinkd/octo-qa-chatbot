FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system app && useradd --system --gid app app \
    && chown app:app /app
COPY --chown=app:app . .
USER app
EXPOSE 8501
CMD ["bash", "exec.sh"]
