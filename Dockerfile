FROM python:3.11-slim

# git: trividia-truemetrix-hid is a git+https dependency, so pip needs git
# to fetch it during install. libhidapi-hidraw0: the runtime library the
# hidapi Python package's compiled extension links against -- same
# requirement as the non-Docker install (see README).
RUN apt-get update && apt-get install --no-install-recommends -y \
        git \
        libhidapi-hidraw0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["trividia-truemetrix-daemon"]
CMD ["--config", "/etc/trividia-truemetrix-daemon/config.ini"]
