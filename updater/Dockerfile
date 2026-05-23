# WattPost updater container — minimal drop-in for Watchtower's HTTP
# API on hosts where Watchtower's Docker SDK lags behind the engine.
#
# Built on docker:27-cli (alpine + docker CLI + compose plugin) so the
# container can shell out to `docker compose pull && up -d` against
# the user's bind-mounted compose file. ~50 MB image.
FROM docker:27-cli

RUN apk add --no-cache python3 docker-cli-compose

COPY updater.py /usr/local/bin/updater.py

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

CMD ["python3", "/usr/local/bin/updater.py"]
