FROM alpine:3.15

WORKDIR /usr/src

RUN apk update \
    && apk add --no-cache python3=3.9.7-r4 py3-pip=20.3.4-r1 smartmontools=7.2-r1 \
    && python3 -m pip install --no-cache-dir prometheus_client==0.14.0 \
    && apk del py3-pip

ADD smartprom.py .

EXPOSE 9902
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENTRYPOINT "./smartprom.py"
