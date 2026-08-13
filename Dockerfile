### The frontend is built here, once, and only its output travels on.
###
### It used to be built at *runtime*: the container ran `npm install` on every start and then
### `npm start`, a create-react-app dev server, in production - because the data was compiled
### into the bundle and a scrape could only reach a browser by recompiling it. The data is
### fetched from /api/data now, so the bundle is a build artefact like any other.
###
### What this stage removes from the running container: node, npm, node_modules (~250 MB), the
### file watcher, two minutes of npm install per start, and a port.
FROM node:20-slim AS frontend-build

WORKDIR /build

### The manifests first, so a change to the source does not invalidate the install layer
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

### Read by the frontend and shown in its header. It has to be set in *this* stage: the build
### bakes it in, and an ARG in the runtime stage would arrive far too late.
### Set with "docker build . --build-arg REACT_APP_VERSION=<version>"
ARG REACT_APP_VERSION
ENV REACT_APP_VERSION=$REACT_APP_VERSION

### CI=true makes react-scripts treat warnings as warnings rather than as an interactive prompt
RUN CI=true npm run build

### ===============================================================================

FROM ubuntu

### Set working directory and copy files
WORKDIR /code
COPY . /code/

### Only the built frontend, not the sources it was built from. .dockerignore keeps
### frontend/node_modules out of the COPY above; this brings back the one directory that matters.
COPY --from=frontend-build /build/build /code/frontend/build

### Make script executable
RUN chmod 770 /code/main.py

### Install dependencies.
###
### No Node.js any more: nothing in the running container executes JavaScript. Flask serves the
### prebuilt bundle from /code/frontend/build.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt update && apt upgrade -y \
    && apt install -y python3 python3-pip curl tree nano tzdata

### Update pip and install dependencies
RUN pip install --upgrade pip && pip install --upgrade -r requirements.txt

### Also read at runtime, by main.py, for the version in the log
ARG REACT_APP_VERSION
ENV REACT_APP_VERSION=$REACT_APP_VERSION

### Set timezone
ENV TZ=Europe/Berlin
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

### Health check.
###
### The container had no health signal at all: Flask could die, the scheduler could stop,
### and the only symptom was a website whose numbers quietly stopped moving.
###
### /api/health answers 200 while the API is up and the scraper is running on schedule -
### including when the last run had a failed stage, because restarting the container
### would not have made Kickbase answer. It answers 503 when no run has completed for far
### longer than RUN_SCHEDULE allows, which is the case a restart does fix.
###
### The start period covers the first main.py run, which walks every player in the competition
### and takes minutes. It used to have to cover two startup sleeps and an npm install on top of
### that; both are gone with the dev server, so Flask now answers within seconds of the start.
HEALTHCHECK --interval=60s --timeout=10s --start-period=600s --retries=3 \
    CMD curl -fsS http://localhost:${FLASK_PORT:-5000}/api/health || exit 1

### Set entrypoint
### https://stackoverflow.com/a/29745541
ENTRYPOINT ["python3", "-u", "/code/entrypoint.py"]
