#!/bin/sh
set -eu

config_store=/run/moodle-config/config.php
if [ -f "$config_store" ] && [ ! -f /var/www/html/config.php ]; then
    cp "$config_store" /var/www/html/config.php
    chown www-data:www-data /var/www/html/config.php
    chmod 0600 /var/www/html/config.php
fi

if [ ! -f /var/www/moodledata/.align-b9-installed ]; then
    php /var/www/html/admin/cli/install.php \
        --non-interactive --agree-license --lang=en \
        --wwwroot="${MOODLE_B9_WWWROOT}" --dataroot=/var/www/moodledata \
        --dbtype=pgsql --dbhost="${MOODLE_B9_DB_HOST}" \
        --dbname="${MOODLE_B9_DB_NAME}" --dbuser="${MOODLE_B9_DB_USER}" \
        --dbpass="${MOODLE_B9_DB_PASSWORD}" --prefix=mdl_ \
        --fullname="ALIGN B9 Moodle Sandbox" --shortname=ALIGN-B9 \
        --adminuser="${MOODLE_B9_ADMIN_USER}" \
        --adminpass="${MOODLE_B9_ADMIN_PASSWORD}" \
        --adminemail="${MOODLE_B9_ADMIN_EMAIL}"
    php /usr/local/bin/moodle-b9-proxy-config.php
    install -o www-data -g www-data -m 0600 /var/www/html/config.php "$config_store"
    touch /var/www/moodledata/.align-b9-installed
    chown www-data:www-data /var/www/moodledata/.align-b9-installed
fi
if [ ! -f /var/www/html/config.php ]; then
    echo 'Moodle installation marker exists but persistent config.php is missing.' >&2
    exit 1
fi
php /usr/local/bin/moodle-b9-seed.php

exec /usr/local/bin/moodle-docker-php-entrypoint "$@"
