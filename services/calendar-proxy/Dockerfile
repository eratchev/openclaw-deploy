FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -r -u 1001 proxy
USER proxy

EXPOSE 8080
CMD ["python", "server.py"]
