FROM python:3.12-slim
WORKDIR /usr/src/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/New_York
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY archive ./archive
COPY slack_archive.py gunicorn.conf.py ./
COPY templates ./templates
COPY static ./static
EXPOSE 5001
CMD ["gunicorn", "--config", "gunicorn.conf.py", "slack_archive:app"]
