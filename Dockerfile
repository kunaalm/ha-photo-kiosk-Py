FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as a non-root user: the engine serves the config/upload API, so it
# must not run as root inside the container.
RUN useradd --create-home --uid 1000 kiosk

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kiosk_py/ kiosk_py/
COPY apps/app.py app.py

# The engine writes config/auth to /config and photos to /photos (bind
# mounts owned by the kiosk user on the host). Give it write access.
RUN mkdir -p /config /photos && chown -R kiosk:kiosk /config /photos /srv
USER kiosk

# Only reachable from the kiosk box itself (localhost). The engine never
# listens on a routable interface.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')"

CMD ["python", "app.py"]