FROM moodlehq/moodle-php-apache@sha256:1db7b6ed013fcb5268095b5e28c410ba9117d38d9b6a324c100c48fcbe69745e

ARG MOODLE_SOURCE_URL=https://packaging.moodle.org/stable405/moodle-4.5.15.tgz
ARG MOODLE_SOURCE_SHA256=f0e09f636c2900806bf208dffdc462f3b6b0ad24163559e9aa9b4f339ddc2096

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*
ADD ${MOODLE_SOURCE_URL} /tmp/moodle-4.5.15.tgz
RUN echo "${MOODLE_SOURCE_SHA256}  /tmp/moodle-4.5.15.tgz" | sha256sum --check --status \
    && mkdir -p /var/www/html /var/www/moodledata \
    && tar -xzf /tmp/moodle-4.5.15.tgz --strip-components=1 -C /var/www/html \
    && rm /tmp/moodle-4.5.15.tgz \
    && chown -R www-data:www-data /var/www/html /var/www/moodledata

COPY scripts/b9/moodle-entrypoint.sh /usr/local/bin/moodle-b9-entrypoint
COPY scripts/b9/moodle_proxy_config.php /usr/local/bin/moodle-b9-proxy-config.php
COPY scripts/b9/seed_moodle.php /usr/local/bin/moodle-b9-seed.php
COPY scripts/b9/register_moodle_tool.php /usr/local/bin/moodle-b9-register-tool.php
COPY scripts/b9/describe_moodle_fixtures.php /usr/local/bin/moodle-b9-fixtures.php
COPY fixtures/b9/moodle-final.pdf /usr/local/share/moodle-b9/final.pdf
COPY fixtures/b9/moodle-final-v2.pdf /usr/local/share/moodle-b9/final-v2.pdf
RUN chmod 0755 /usr/local/bin/moodle-b9-entrypoint \
    && chmod 0644 /usr/local/bin/moodle-b9-proxy-config.php /usr/local/bin/moodle-b9-seed.php \
        /usr/local/bin/moodle-b9-register-tool.php /usr/local/bin/moodle-b9-fixtures.php

EXPOSE 80
ENTRYPOINT ["/usr/local/bin/moodle-b9-entrypoint"]
CMD ["apache2-foreground"]
