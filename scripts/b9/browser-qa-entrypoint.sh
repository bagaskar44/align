#!/usr/bin/env bash
set -euo pipefail

ca_file=/run/b9/ca.crt
if [[ ! -s "$ca_file" ]]; then
  echo "B9 QA CA is unavailable." >&2
  exit 2
fi
install -m 0644 "$ca_file" /usr/local/share/ca-certificates/align-b9-ca.crt
update-ca-certificates --fresh >/dev/null
export NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/align-b9-ca.crt

mkdir -p "$HOME/.pki/nssdb" /run/b9-qa/artifacts
if [[ ! -f "$HOME/.pki/nssdb/cert9.db" ]]; then
  certutil -N --empty-password -d "sql:$HOME/.pki/nssdb"
fi
certutil -D -d "sql:$HOME/.pki/nssdb" -n "ALIGN B9 local CA" >/dev/null 2>&1 || true
certutil -A -d "sql:$HOME/.pki/nssdb" -n "ALIGN B9 local CA" -t "C,," -i "$ca_file"

proxy_ip="$(getent ahostsv4 proxy | awk 'NR == 1 {print $1}')"
if [[ -z "$proxy_ip" ]]; then
  echo "B9 QA could not resolve the isolated proxy." >&2
  exit 2
fi
export B9_PROXY_IP="$proxy_ip"

exec node /run/b9-config/browser_acceptance.cjs
