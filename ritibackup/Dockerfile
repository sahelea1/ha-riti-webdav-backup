ARG BUILD_FROM
FROM ${BUILD_FROM}

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install Python and the native dependencies from Alpine's prebuilt packages.
# Using the apk wheels for cryptography and aiohttp avoids having to compile
# Rust/C extensions at build time, which keeps the image small and the build
# fast and reliable across all architectures.
RUN apk add --no-cache \
        python3 \
        py3-pip \
        py3-cryptography \
        py3-aiohttp

# S3 (boto3) and Backblaze B2 (b2sdk) backends. Both are pure-Python and pull
# in only pure-Python deps, so no compilers are needed.
RUN pip3 install --no-cache-dir --break-system-packages boto3 b2sdk

# Application code
WORKDIR /opt/ritibackup
COPY app/ ./app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Ingress port (must match config.yaml ingress_port and app/__main__.py)
EXPOSE 8099

LABEL \
    io.hass.name="RitiBackup" \
    io.hass.description="Home Assistant backups to WebDAV, S3, and Backblaze B2" \
    io.hass.type="addon" \
    io.hass.version="1.1.0"

CMD [ "/run.sh" ]
