FROM python:alpine
WORKDIR /usr/src/app
RUN set -eux \
    && apk add --no-cache --virtual .build-deps build-base \
        libffi-dev gcc musl-dev python3-dev \
        mariadb-dev mariadb-client tzdata \
    && pip install --upgrade pip setuptools wheel \
    && rm -rf /root/.cache/pip
COPY ./requirements.txt /usr/src/app/requirements.txt
RUN set -eux\
    && pip install --root-user-action=ignore -r /usr/src/app/requirements.txt \
    && rm -rf /root/.cache/pip
COPY . /usr/src/app
RUN rm -f /usr/src/app/config.py && rm -rf /usr/src/app/templates && rm -rf /usr/src/app/static
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV FLASK_APP /usr/src/app/slack_archive.py
ENV TZ America/New_York
ENV FLASK_DEBUG 1
CMD ["python3", "-u", "/usr/local/bin/flask", "run", "--host", "0.0.0.0", "--port", "5001"]
