FROM debian:bookworm-slim
ARG VERSION=dev
LABEL org.opencontainers.image.title="fritzbox-portforward" \
      org.opencontainers.image.description="Auto-manages FRITZ!Box UPnP port forwards for labelled Docker containers, with an optional pfSense API bridge" \
      org.opencontainers.image.source="https://github.com/merschie/fritzbox-portforward" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-docker python3-miniupnpc openssl \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY LICENSE portforward.py pfsense_bridge.py test_parse.py /app/
CMD ["python3", "/app/portforward.py"]
