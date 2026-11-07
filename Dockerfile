FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY app.py ./
COPY airisk ./airisk
COPY migrations ./migrations
COPY templates ./templates
COPY static ./static
COPY entrypoint.sh ./
RUN mkdir -p /app/instance && chown -R appuser:appuser /app && chmod +x /app/entrypoint.sh
USER appuser
VOLUME /app/instance
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "60", "app:create_app()"]
