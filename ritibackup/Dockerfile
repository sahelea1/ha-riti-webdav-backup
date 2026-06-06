ARG BUILD_FROM
FROM ${BUILD_FROM}

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install Python and the two native dependencies from Alpine's prebuilt
# packages. Using the apk wheels for cryptography and aiohttp avoids having to
# compile Rust/C extensions at build time, which keeps the image small and the
# build fast and reliable across all architectures.
RUN apk add --no-cache \
        python3 \
        py3-cryptography \
        py3-aiohttp

# Application code
WORKDIR /opt/ritibackup
COPY app/ ./app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Ingress port (must match config.yaml ingress_port and app/__main__.py)
EXPOSE 8099

LABEL \
    io.hass.name="RitiBackup" \
    io.hass.description="Encrypted Home Assistant backups to WebDAV" \
    io.hass.type="addon" \
    io.hass.version="1.0.0"

CMD [ "/run.sh" ]
