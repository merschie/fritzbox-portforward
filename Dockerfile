FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-docker python3-miniupnpc openssl \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY portforward.py pfsense_bridge.py test_parse.py /app/
CMD ["python3", "/app/portforward.py"]
