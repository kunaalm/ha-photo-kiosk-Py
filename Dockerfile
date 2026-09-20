FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kiosk_py/ kiosk_py/
COPY apps/app.py app.py

# Only reachable from the kiosk box itself (localhost). The engine never
# listens on a routable interface.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')"

CMD ["python", "app.py"]