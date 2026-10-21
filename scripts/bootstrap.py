"""Create private development environments on macOS/Linux without PowerShell."""

import argparse
import os
import re
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=3000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    root = Path(__file__).resolve().parent.parent
    paths = [root / name for name in (
        '.env', '.env.api', '.env.worker', '.env.migration', '.env.postgres', '.env.tests'
    )]
    if any(path.exists() for path in paths):
        raise SystemExit('Environment files already exist; preserve existing credentials.')

    template = (root / '.env.example').read_text()
    for placeholder in (
        'replace-with-random-secret',
        'replace-with-at-least-32-random-bytes',
        'replace-with-random-development-password',
    ):
        template = template.replace(placeholder, secrets.token_hex(32))
    values = dict(
        line.split('=', 1) for line in template.splitlines()
        if line and not line.startswith('#')
    )
    for role in ('POSTGRES', 'MIGRATION_DB', 'API_DB', 'WORKER_DB', 'OPERATOR_DB'):
        values[role + '_PASSWORD'] = secrets.token_hex(32)
    for key, role in (
        ('DATABASE_URL', 'api'), ('WORKER_DATABASE_URL', 'worker'),
        ('MIGRATION_DATABASE_URL', 'migration'), ('OPERATOR_DATABASE_URL', 'operator'),
    ):
        password = values[role.upper() + '_DB_PASSWORD']
        values[key] = f'postgresql+psycopg://align_{role}:{password}@postgres:5432/align'
    values['S3_ACCESS_KEY_ID'] = 'align-' + secrets.token_hex(16)
    values['S3_SECRET_ACCESS_KEY'] = secrets.token_hex(32)
    values['APP_ORIGIN'] = f'http://localhost:{args.port}'
    values['ALIGN_HOST_PORT'] = str(args.port)

    def write(name: str, data: dict[str, str]) -> None:
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(''.join(f'{key}={value}\n' for key, value in data.items()))

    # Reuse the canonical service allowlists rather than duplicating role policy.
    powershell = (root / 'scripts/bootstrap.ps1').read_text()
    environments = {}
    for group in ('api', 'worker', 'migration', 'postgres'):
        match = re.search(r'\$' + group + r'Names = @\((.*?)\n\)', powershell, re.S)
        if match is None:
            raise SystemExit(f'Missing canonical environment allowlist: {group}')
        keys = re.findall(r"'([A-Z][A-Z0-9_]*)'", match.group(1))
        environments['.env.' + group] = {key: values[key] for key in keys}
    environments['.env.tests'] = {
        'DATABASE_URL': values['DATABASE_URL'],
        'WORKER_DATABASE_URL': values['WORKER_DATABASE_URL'],
        'ALIGN_RUN_INTEGRATION': 'true',
    }
    write('.env', values)
    for name, data in environments.items():
        write(name, data)
    print('Created private mock/synthetic environment files (mode 0600).')
    print('Demo password: ALIGN_SEED_PASSWORD in .env; secrets were not printed.')


if __name__ == '__main__':
    main()
