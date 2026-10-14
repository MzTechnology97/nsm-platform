# New React interface (/app): built here, copied into the Python image as static files.
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /code
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app
COPY requirements.txt /code/requirements.txt
RUN pip install --upgrade pip && pip install -r /code/requirements.txt
COPY . /code
COPY --from=ui /ui/dist /code/app/static/ui
RUN chown -R app:app /code
USER app
EXPOSE 8000
