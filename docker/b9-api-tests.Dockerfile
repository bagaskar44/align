FROM align-b9-sandbox-api:latest

USER root
WORKDIR /app

COPY tests/unit/test_b9_integrations_contract.py tests/unit/test_b9_integrations_contract.py
COPY tests/unit/test_b9_lti.py tests/unit/test_b9_lti.py
COPY tests/unit/test_b9_moodle_adapter.py tests/unit/test_b9_moodle_adapter.py
COPY scripts/b9/seed_align_fixtures.py scripts/b9/seed_align_fixtures.py
COPY scripts/b9/create_fixture_pdf.py scripts/b9/create_fixture_pdf.py
COPY scripts/openapi_contract.py scripts/openapi_contract.py
COPY docs/api/openapi.json docs/api/openapi.json
COPY apps/web/src/lib/api-contract.generated.ts apps/web/src/lib/api-contract.generated.ts

RUN uv pip install --python /app/.venv/bin/python pytest==8.4.2 mypy==1.20.2 ruff==0.17.1

CMD ["uv", "run", "--no-sync", "--package", "align-api", "pytest", "-q", "tests/unit/test_b9_integrations_contract.py", "tests/unit/test_b9_lti.py", "tests/unit/test_b9_moodle_adapter.py"]
