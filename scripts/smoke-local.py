"""Verify the seeded local mock runtime without printing credentials or session tokens."""

import argparse
import http.cookiejar
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--docker', default='docker')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    values = dict(
        line.split('=', 1) for line in (root / '.env').read_text().splitlines()
        if line and not line.startswith('#')
    )
    if values['PROVIDER_MODE'] != 'mock' or values['DATA_MODE'] != 'synthetic':
        raise SystemExit('Smoke requires mock/synthetic mode.')
    origin = values['APP_ORIGIN']
    parsed_origin = urllib.parse.urlsplit(origin)
    if (parsed_origin.scheme != 'http' or parsed_origin.hostname != 'localhost'
            or parsed_origin.port is None or parsed_origin.username is not None
            or parsed_origin.password is not None or parsed_origin.path
            or parsed_origin.query or parsed_origin.fragment):
        raise SystemExit('Smoke is restricted to the local development origin.')

    def seed() -> dict[str, object]:
        command = [args.docker, 'compose', '--project-name', args.project,
                   'exec', '-T', 'api', 'align', 'seed']
        # Fixed CLI arguments; neither password nor token is passed through argv.
        result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)  # noqa: S603
        return json.loads(result.stdout)  # type: ignore[no-any-return]

    first, second = seed(), seed()
    if first['status'] != 'ready' or second['status'] != 'ready':
        raise SystemExit('Seed readiness failed.')
    if any(second[key] != 0 for key in ('accounts_created', 'courses_created', 'memberships_created')):
        raise SystemExit('Seed is not idempotent.')
    print('Synthetic seed rerun: no duplicate accounts/courses/memberships.')

    for email in ('dosen@align.local', 'mahasiswa@align.local', 'admin@align.local'):
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.ProxyHandler({}),
        )

        def request(path: str, method: str = 'GET', data: object = None,
                    csrf: str | None = None,
                    client: urllib.request.OpenerDirector = opener) -> tuple[int, bytes]:
            headers = {'Origin': origin}
            if csrf is not None:
                headers['X-CSRF-Token'] = csrf
            body = None if data is None else json.dumps(data).encode()
            if body is not None:
                headers['Content-Type'] = 'application/json'
            # The origin is checked above; no external URL or document input is accepted.
            req = urllib.request.Request(origin + path, data=body, headers=headers, method=method)  # noqa: S310
            with client.open(req, timeout=15) as response:
                return response.status, response.read()

        status, _ = request('/login')
        if status != 200:
            raise SystemExit('Login page unavailable.')
        _, health = request('/health/ready')
        if json.loads(health)['status'] != 'ready':
            raise SystemExit('Runtime is not ready.')
        request('/api/v1/auth/login', 'POST', {'email': email, 'password': values['ALIGN_SEED_PASSWORD']})
        _, profile_body = request('/api/v1/me')
        profile = json.loads(profile_body)
        if profile['user']['email'] != email:
            raise SystemExit('Unexpected signed-in identity.')
        request('/api/v1/courses')
        request('/api/v1/auth/logout', 'POST', csrf=profile['csrf_token'])
        try:
            request('/api/v1/me')
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise SystemExit('Unexpected status after logout.') from None
        else:
            raise SystemExit('Session remains valid after logout.')
        print(f'{email}: login, profile, courses, logout verified.')
    print('HTTP login page and database/broker/storage readiness verified. Parser coverage is reported separately in docs/handover/B1.md.')


if __name__ == '__main__':
    main()
