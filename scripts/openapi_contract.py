from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "docs/api/openapi.json"
CLIENT_PATH = ROOT / "apps/web/src/lib/api-contract.generated.ts"
INTERNAL_WEB_PATHS = {"/health/providers"}
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def current_openapi() -> dict[str, Any]:
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("PROVIDER_MODE", "mock")
    from align_api.main import app

    return app.openapi()


def _schema_type(schema: dict[str, Any], indent: int = 0) -> str:
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        return f'ApiSchemas[{json.dumps(name)}]'
    for union_key in ("oneOf", "anyOf"):
        union = schema.get(union_key)
        if isinstance(union, list):
            return " | ".join(f"({_schema_type(item, indent)})" for item in union)
    intersection = schema.get("allOf")
    if isinstance(intersection, list):
        return " & ".join(f"({_schema_type(item, indent)})" for item in intersection)
    if "enum" in schema and isinstance(schema["enum"], list):
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in schema["enum"])
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(_schema_type({**schema, "type": item}, indent) for item in schema_type)
    if schema_type == "array":
        item = schema.get("items", {})
        return f"Array<{_schema_type(item if isinstance(item, dict) else {}, indent)}>"
    properties = schema.get("properties")
    if schema_type == "object" or isinstance(properties, dict):
        properties = properties if isinstance(properties, dict) else {}
        required = set(schema.get("required", []))
        padding = " " * (indent + 2)
        fields = [
            f"{padding}{json.dumps(name, ensure_ascii=False)}{'?' if name not in required else ''}: {_schema_type(child, indent + 2)};"
            for name, child in sorted(properties.items())
            if isinstance(child, dict)
        ]
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            fields.append(f"{padding}[key: string]: {_schema_type(additional, indent + 2)};")
        elif additional is True:
            fields.append(f"{padding}[key: string]: unknown;")
        if not fields:
            return "Record<string, unknown>" if additional is not False else "Record<string, never>"
        return "{\n" + "\n".join(fields) + f"\n{' ' * indent}}}"
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    return "unknown"


def _operation_body(operation: dict[str, Any], *, request: bool, schemas: dict[str, Any]) -> str:
    if request:
        content = operation.get("requestBody", {}).get("content", {})
        for media_type in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
            value = content.get(media_type, {}).get("schema")
            if isinstance(value, dict):
                return _schema_type(value)
        return "void"
    responses = operation.get("responses", {})
    success_codes = sorted(code for code in responses if code[:1] == "2" and code != "204")
    for code in success_codes:
        content = responses[code].get("content", {})
        for media_type in ("application/json", "text/plain"):
            value = content.get(media_type, {}).get("schema")
            if isinstance(value, dict):
                return _schema_type(value)
    return "void"


def render_typescript(spec: dict[str, Any]) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    schema_entries = "\n".join(
        f'  {json.dumps(name, ensure_ascii=False)}: {_schema_type(schema, 2)};'
        for name, schema in sorted(schemas.items())
        if isinstance(schema, dict)
    )
    path_methods: dict[str, list[str]] = {}
    operation_entries: list[str] = []
    for api_path, operations in sorted(spec["paths"].items()):
        path = api_path.removeprefix("/api/v1") if api_path.startswith("/api/v1") else api_path
        path_methods[path] = sorted(method.upper() for method in operations if method in METHODS)
        operation_lines = []
        for method in path_methods[path]:
            operation = operations[method.lower()]
            operation_id = json.dumps(operation.get("operationId", ""), ensure_ascii=False)
            request_body = _operation_body(operation, request=True, schemas=schemas)
            response_body = _operation_body(operation, request=False, schemas=schemas)
            operation_lines.append(
                f"    {method}: {{ operationId: {operation_id}; requestBody: {request_body}; response: {response_body} }};"
            )
        operation_entries.append(f'  {json.dumps(path, ensure_ascii=False)}: {{\n' + "\n".join(operation_lines) + "\n  };")
    entries = "\n".join(
        f'  "{path}": {" | ".join(json.dumps(method) for method in methods)};'
        for path, methods in path_methods.items()
    )
    return f'''/* Generated by scripts/openapi_contract.py. Do not edit by hand. */
export type ApiSchemas = {{
{schema_entries}
}};

export interface ApiOperations {{
{chr(10).join(operation_entries)}
}}

export interface ApiOperationMethods {{
{entries}
}}

type ApiOpenApiPath = keyof ApiOperationMethods & string;
type ApiPathPattern<Path extends string> = Path extends `${{infer Before}}{{${{string}}}}${{infer After}}`
  ? `${{Before}}${{string}}${{ApiPathPattern<After>}}`
  : Path;
type ApiRoute = {{ [Path in ApiOpenApiPath]: ApiPathPattern<Path> }}[ApiOpenApiPath];
type StripQuery<Path extends string> = Path extends `${{infer Route}}?${{string}}` ? Route : Path;

export type ApiRequestPath = ApiRoute | `${{ApiRoute}}?${{string}}`;
export type ApiMethod = ApiOperationMethods[ApiOpenApiPath];
export type ApiMethodForPath<Path extends string> = {{
  [Route in ApiOpenApiPath]: StripQuery<Path> extends ApiPathPattern<Route>
    ? ApiOperationMethods[Route]
    : never;
}}[ApiOpenApiPath];
export type ApiRequestBodyForPath<Path extends string, Method extends string> = {{
  [Route in ApiOpenApiPath]: StripQuery<Path> extends ApiPathPattern<Route>
    ? Method extends keyof ApiOperations[Route]
      ? ApiOperations[Route][Method] extends {{ requestBody: infer Body }} ? Body : never
      : never
    : never;
}}[ApiOpenApiPath];
export type ApiResponseBodyForPath<Path extends string, Method extends string> = {{
  [Route in ApiOpenApiPath]: StripQuery<Path> extends ApiPathPattern<Route>
    ? Method extends keyof ApiOperations[Route]
      ? ApiOperations[Route][Method] extends {{ response: infer Body }} ? Body : never
      : never
    : never;
}}[ApiOpenApiPath];
'''


def path_matches(route: str, requested: str) -> bool:
    route_segments = route.strip("/").split("/")
    requested_segments = requested.strip("/").split("/")
    return len(route_segments) == len(requested_segments) and all(
        (segment.startswith("{") and segment.endswith("}")) or segment == actual
        for segment, actual in zip(route_segments, requested_segments, strict=True)
    )


def web_api_references(spec: dict[str, Any]) -> list[str]:
    openapi_paths = set(spec["paths"])
    failures: list[str] = []
    source_root = ROOT / "apps/web/src"
    for path in sorted(source_root.rglob("*.ts")) + sorted(source_root.rglob("*.tsx")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"/api/v1([^\"'`\s]*)", source):
            suffix = match.group(1)
            if not suffix or suffix.startswith("${"):
                # Templated helper calls are type-checked against ApiRequestPath.
                continue
            requested = "/api/v1" + suffix
            requested = requested.split("?", 1)[0]
            requested = re.sub(r"\$\{[^}]+\}", "generated-id", requested)
            if requested == "/api/v1/health/providers":
                if requested.removeprefix("/api/v1") not in INTERNAL_WEB_PATHS:
                    failures.append(f"{path.relative_to(ROOT)}: unlisted internal route {requested}")
                continue
            if not any(path_matches(route, requested) for route in openapi_paths):
                failures.append(f"{path.relative_to(ROOT)}: API path is absent from OpenAPI: {requested}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the server OpenAPI snapshot and typed web path contract")
    parser.add_argument("--check", action="store_true", help="fail when checked-in contract artifacts differ")
    args = parser.parse_args()
    spec = current_openapi()
    openapi_body = json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    typescript_body = render_typescript(spec)
    failures = web_api_references(spec)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    if args.check:
        stale = [
            path.relative_to(ROOT).as_posix()
            for path, expected in ((OPENAPI_PATH, openapi_body), (CLIENT_PATH, typescript_body))
            if not path.exists() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            print("Generated API contracts are stale: " + ", ".join(stale), file=sys.stderr)
            return 1
        print(f"OpenAPI/client contract verified: {len(spec['paths'])} server paths")
        return 0
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(openapi_body, encoding="utf-8")
    CLIENT_PATH.write_text(typescript_body, encoding="utf-8")
    print(f"Generated OpenAPI snapshot and typed web path contract for {len(spec['paths'])} server paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
