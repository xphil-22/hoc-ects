FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html ./
ENV HOST=0.0.0.0 PORT=8765 CACHE_FILE=/data/cache.json PYTHONUNBUFFERED=1
RUN mkdir /data && useradd -r app && chown app /data
USER app
VOLUME /data
EXPOSE 8765
CMD ["python3", "server.py"]
