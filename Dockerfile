FROM python:3.15-slim@sha256:f0aecb335180611a3a000c45def529ec8ee768d5d26ff24bae4dfb70594a9121

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY exporter.py .

EXPOSE 9835
CMD ["python", "/app/exporter.py"]
