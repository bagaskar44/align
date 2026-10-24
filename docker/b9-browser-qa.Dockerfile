FROM mcr.microsoft.com/playwright:v1.64.1-noble@sha256:47b510dd9230bf0b9bfc9b20102241e577ea7a99334ab4c3ca19f19770e9b8ea

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libnss3-tools \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --no-audit --no-fund playwright@1.64.1 \
    && npm cache clean --force

ENV NODE_PATH=/usr/lib/node_modules \
    HOME=/home/pwuser \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY scripts/b9/browser-qa-entrypoint.sh /usr/local/bin/b9-browser-qa-entrypoint
RUN chmod 0755 /usr/local/bin/b9-browser-qa-entrypoint

ENTRYPOINT ["/usr/local/bin/b9-browser-qa-entrypoint"]
