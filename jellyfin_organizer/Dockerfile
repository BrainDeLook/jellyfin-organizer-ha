FROM alpine:3.24
ARG BUILD_VERSION=dev
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.type="app" \
      org.opencontainers.image.source="https://github.com/BrainDeLook/jellyfin-organizer-ha"
RUN apk add --no-cache python3 ffmpeg ca-certificates tini \
    && apk add --no-cache --virtual .python-build py3-pip
WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/organizer \
    && /opt/organizer/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/organizer/bin/python -c "from guessit import guessit; assert guessit('Show.S01E01.mkv')['episode'] == 1" \
    && ffmpeg -version >/dev/null && ffprobe -version >/dev/null \
    && apk del .python-build
COPY organizer.py media.py web.py ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/sbin/tini", "-g", "--"]
CMD ["/opt/organizer/bin/python", "web.py"]
