FROM python:3.11-slim

WORKDIR /app

# Version label only -- the source always comes from src/. (Before this repo
# used git, this arg selected which v_*/ folder to copy.)
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

ENV PYTHONPATH=/app

EXPOSE 5000

ENTRYPOINT ["python", "app.py"]
