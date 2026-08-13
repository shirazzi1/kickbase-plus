FROM ubuntu 

### Set working directory and copy files
WORKDIR /code
COPY . /code/

### Make script executable
RUN chmod 770 /code/main.py

### Install dependencies
ARG DEBIAN_FRONTEND=noninteractive
RUN apt update && apt upgrade -y \
    && apt install -y python3 python3-pip curl tree nano tzdata

### Installs Node.js and npm directly
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs

### Update pip and install dependencies
RUN pip install --upgrade pip && pip install --upgrade -r requirements.txt

### Set environment variables / build arguments
### Will later be read from frontend
### Can be set with "docker build . -t ghcr.io/casudo/kickbase-insights:<version> --build-arg REACT_APP_VERSION=<version>"
ARG REACT_APP_VERSION 
ENV REACT_APP_VERSION=$REACT_APP_VERSION

ENV WATCHPACK_POLLING=true

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
### The start period has to cover the first main.py run plus both startup sleeps. A cold
### run walks every player in the competition and takes minutes, and Flask is the last
### thing to come up.
HEALTHCHECK --interval=60s --timeout=10s --start-period=900s --retries=3 \
    CMD curl -fsS http://localhost:5000/api/health || exit 1

### Set entrypoint
### https://stackoverflow.com/a/29745541
ENTRYPOINT ["python3", "-u", "/code/entrypoint.py"]