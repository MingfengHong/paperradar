FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -e .

EXPOSE 8766
CMD ["paperradar", "schedule", "daemon", "--interval", "1800"]
