FROM minio/minio:RELEASE.2025-09-07T16-13-09Z

ENV MINIO_ROOT_USER=minio \
    MINIO_VOLUMES=/data

EXPOSE 9000 9001

CMD ["server", "/data", "--console-address", ":9001"]
