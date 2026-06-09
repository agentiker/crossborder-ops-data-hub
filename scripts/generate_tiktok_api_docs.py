"""Generate TikTok Shop API documentation from the bundled OpenAPI spec."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _clip(text: Any, limit: int = 260) -> str:
    if text is None:
        return ""
    value = " ".join(str(text).split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _schema_ref(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    return None


class OpenApiDocBuilder:
    def __init__(self, spec: dict[str, Any], *, max_depth: int = 5) -> None:
        self.spec = spec
        self.schemas = spec.get("components", {}).get("schemas", {})
        self.max_depth = max_depth

    def resolve_ref(self, ref: str) -> dict[str, Any]:
        return self.schemas[_ref_name(ref)]

    def type_label(self, schema: dict[str, Any] | None) -> str:
        if not schema:
            return "object"
        if "$ref" in schema:
            return _ref_name(schema["$ref"])
        if "allOf" in schema:
            return "allOf[" + ", ".join(self.type_label(item) for item in schema["allOf"]) + "]"
        if "oneOf" in schema:
            return "oneOf[" + ", ".join(self.type_label(item) for item in schema["oneOf"]) + "]"
        if "anyOf" in schema:
            return "anyOf[" + ", ".join(self.type_label(item) for item in schema["anyOf"]) + "]"
        if schema.get("type") == "array":
            return f"array<{self.type_label(schema.get('items'))}>"
        if schema.get("format"):
            return f"{schema.get('type', 'object')}:{schema['format']}"
        return schema.get("type", "object")

    def flatten_schema(
        self,
        schema: dict[str, Any] | None,
        *,
        prefix: str = "",
        depth: int = 0,
        seen: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not schema:
            return []
        seen = set(seen or set())

        if "$ref" in schema:
            ref = schema["$ref"]
            name = _ref_name(ref)
            if name in seen:
                return [{
                    "field": prefix or name,
                    "type": name,
                    "required": False,
                    "description": "recursive reference",
                    "schema": name,
                }]
            return self.flatten_schema(
                self.resolve_ref(ref),
                prefix=prefix,
                depth=depth,
                seen=seen | {name},
            )

        rows: list[dict[str, Any]] = []
        for key in ("allOf", "oneOf", "anyOf"):
            if key in schema:
                for item in schema[key]:
                    rows.extend(
                        self.flatten_schema(
                            item,
                            prefix=prefix,
                            depth=depth,
                            seen=seen,
                        )
                    )
                return rows

        if schema.get("type") == "array":
            item_prefix = f"{prefix}[]" if prefix else "[]"
            return self.flatten_schema(
                schema.get("items"),
                prefix=item_prefix,
                depth=depth,
                seen=seen,
            ) or [{
                "field": item_prefix,
                "type": self.type_label(schema.get("items")),
                "required": False,
                "description": _clip(schema.get("description")),
                "schema": _schema_ref(schema.get("items")),
            }]

        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if not properties:
            if prefix:
                return [{
                    "field": prefix,
                    "type": self.type_label(schema),
                    "required": False,
                    "description": _clip(schema.get("description")),
                    "schema": _schema_ref(schema),
                }]
            return []

        for name, child in properties.items():
            field = f"{prefix}.{name}" if prefix else name
            row = {
                "field": field,
                "type": self.type_label(child),
                "required": name in required,
                "description": _clip(child.get("description")),
                "schema": _schema_ref(child),
            }
            rows.append(row)
            if depth < self.max_depth and (
                "$ref" in child
                or child.get("type") == "object"
                or child.get("type") == "array"
                or any(k in child for k in ("allOf", "oneOf", "anyOf"))
            ):
                rows.extend(
                    self.flatten_schema(
                        child,
                        prefix=field,
                        depth=depth + 1,
                        seen=seen,
                    )
                )
        return rows

    def parameters(self, operation: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for param in operation.get("parameters") or []:
            schema = param.get("schema") or {}
            rows.append({
                "name": param.get("name"),
                "in": param.get("in"),
                "required": bool(param.get("required")),
                "type": self.type_label(schema),
                "description": _clip(param.get("description")),
                "example": param.get("example"),
            })
        return rows

    def body_schema(self, operation: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
        content = (operation.get("requestBody") or {}).get("content") or {}
        media = content.get("application/json") or next(iter(content.values()), {})
        schema = media.get("schema")
        return _schema_ref(schema), self.flatten_schema(schema)

    def response_schema(self, operation: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
        responses = operation.get("responses") or {}
        response = responses.get("200") or responses.get("201") or next(iter(responses.values()), {})
        content = response.get("content") or {}
        media = content.get("application/json") or next(iter(content.values()), {})
        schema = media.get("schema")
        return _schema_ref(schema), self.flatten_schema(schema)

    def build_index(self) -> dict[str, Any]:
        operations = []
        for path, path_item in sorted(self.spec.get("paths", {}).items()):
            for method, operation in sorted(path_item.items()):
                if method.lower() not in HTTP_METHODS:
                    continue
                request_schema, request_fields = self.body_schema(operation)
                response_schema, response_fields = self.response_schema(operation)
                tags = operation.get("tags") or []
                operations.append({
                    "method": method.upper(),
                    "path": path,
                    "tag": tags[0] if tags else "",
                    "summary": operation.get("summary") or "",
                    "description": _clip(operation.get("description"), 600),
                    "operation_id": operation.get("operationId") or "",
                    "parameters": self.parameters(operation),
                    "request_schema": request_schema,
                    "request_fields": request_fields,
                    "response_schema": response_schema,
                    "response_fields": response_fields,
                })
        return {
            "source": "material/go_sdk_extracted/api/openapi.yaml",
            "server": (self.spec.get("servers") or [{}])[0].get("url", ""),
            "operation_count": len(operations),
            "schema_count": len(self.schemas),
            "operations": operations,
        }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cleaned = [str(value if value is not None else "").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(cleaned) + " |")
    return "\n".join(lines)


def _field_table(fields: list[dict[str, Any]], *, limit: int = 80) -> str:
    if not fields:
        return "_None._"
    rows = []
    for field in fields[:limit]:
        rows.append([
            field["field"],
            field["type"],
            "yes" if field["required"] else "no",
            field.get("schema") or "",
            field.get("description") or "",
        ])
    table = _md_table(["field", "type", "required", "schema", "description"], rows)
    if len(fields) > limit:
        table += f"\n\n_Only first {limit} fields shown in Markdown; full fields are in JSON index._"
    return table


def render_markdown(index: dict[str, Any]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in index["operations"]:
        grouped[operation["tag"] or "untagged"].append(operation)

    lines = [
        "# TikTok Shop Open API Reference",
        "",
        "Generated from bundled TikTok Shop OpenAPI/Go SDK material.",
        "",
        f"- Source: `{index['source']}`",
        f"- Server: `{index['server']}`",
        f"- Operations: `{index['operation_count']}`",
        f"- Schemas: `{index['schema_count']}`",
        "",
        "## Operation Summary",
        "",
    ]

    summary_rows = [
        [tag, len(operations)]
        for tag, operations in sorted(grouped.items())
    ]
    lines.append(_md_table(["tag", "operation_count"], summary_rows))
    lines.append("")

    for tag, operations in sorted(grouped.items()):
        lines.extend([f"## {tag}", ""])
        lines.append(_md_table(
            ["method", "path", "summary"],
            [[op["method"], f"`{op['path']}`", op["summary"]] for op in operations],
        ))
        lines.append("")
        for op in operations:
            lines.extend([
                f"### {op['method']} {op['path']}",
                "",
                f"- Summary: {op['summary'] or '-'}",
                f"- Operation ID: `{op['operation_id'] or '-'}`",
                f"- Description: {op['description'] or '-'}",
                f"- Request schema: `{op['request_schema'] or '-'}`",
                f"- Response schema: `{op['response_schema'] or '-'}`",
                "",
                "#### Parameters",
                "",
            ])
            lines.append(_md_table(
                ["name", "in", "required", "type", "description"],
                [
                    [
                        param["name"],
                        param["in"],
                        "yes" if param["required"] else "no",
                        param["type"],
                        param["description"],
                    ]
                    for param in op["parameters"]
                ],
            ) if op["parameters"] else "_None._")
            lines.extend(["", "#### Request Fields", ""])
            lines.append(_field_table(op["request_fields"]))
            lines.extend(["", "#### Response Fields", ""])
            lines.append(_field_table(op["response_fields"]))
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--openapi",
        default="material/go_sdk_extracted/api/openapi.yaml",
        help="Path to TikTok Shop OpenAPI YAML.",
    )
    parser.add_argument(
        "--markdown",
        default="docs/tiktok-shop-openapi-reference.md",
        help="Markdown output path.",
    )
    parser.add_argument(
        "--json",
        default="docs/tiktok-shop-openapi-index.json",
        help="JSON index output path.",
    )
    args = parser.parse_args()

    with Path(args.openapi).open() as file:
        spec = yaml.safe_load(file)

    builder = OpenApiDocBuilder(spec)
    index = builder.build_index()

    markdown_path = Path(args.markdown)
    json_path = Path(args.json)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    markdown_path.write_text(render_markdown(index), encoding="utf-8")
    json_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(f"Wrote {markdown_path} ({index['operation_count']} operations)")
    print(f"Wrote {json_path} ({index['schema_count']} schemas)")


if __name__ == "__main__":
    main()
