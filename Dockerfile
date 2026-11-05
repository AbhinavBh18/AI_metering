FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt \
    && useradd --create-home metering
COPY app ./app
COPY migrations ./migrations
COPY config.py ./
RUN mkdir -p /app/data && chown -R metering:metering /app
USER metering
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"
CMD ["python", "-m", "app", "run", "--host", "0.0.0.0"]
