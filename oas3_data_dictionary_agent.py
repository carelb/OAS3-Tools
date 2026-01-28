
#!/usr/bin/env python3
"""
OAS3 Data Dictionary Agent
--------------------------
Creates a data dictionary (Excel + optional CSV) from an OpenAPI 3.x document.

Features:
- Accepts YAML or JSON from a URL or local file
- Extracts request parameters, response payload properties, and schema components
- Supports oneOf / anyOf / allOf merging
- Captures type, description, constraints (pattern, enum, min/max, multipleOf, format, required)
- Includes example values, and schema path context
- Preserves all occurrences (dedupe includes object id)
- Increased recursion limits for very deep schemas
"""

import argparse
import json
import sys
import re
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import yaml
import requests
import pandas as pd

# ----------------------------------------------------
# Allow deep OpenAPI schemas
# ----------------------------------------------------
sys.setrecursionlimit(10000)


# ----------------------------------------------------
# ------------------- Core Helpers -------------------
# ----------------------------------------------------

def load_oas3(source: str) -> Dict[str, Any]:
    """Load OpenAPI doc from URL or local file."""
    if re.match(r"^https?://", source, flags=re.I):
        resp = requests.get(source, timeout=60)
        resp.raise_for_status()
        text = resp.text
    else:
        with open(source, "r", encoding="utf-8") as f:
            text = f.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def resolve_ref(doc: Dict[str, Any], ref: str) -> Dict[str, Any]:
    """Resolve a $ref safely."""
    if not ref or not ref.startswith("#/"):
        return {}
    node: Any = doc
    try:
        for part in ref.lstrip("#/").split("/"):
            node = node[part]
        return node if isinstance(node, dict) else {}
    except Exception:
        return {}


def safe_join(*parts: Optional[str]) -> str:
    return " | ".join([p for p in parts if p])


def normalise_type(schema: Dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return ""
    t = schema.get("type")
    fmt = schema.get("format")
    if t and fmt:
        return f"{t} ({fmt})"
    return t or ("$ref" if "$ref" in schema else "")


def extract_example(schema: Dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return ""
    if "example" in schema:
        return json.dumps(schema["example"], ensure_ascii=False)
    if "examples" in schema:
        try:
            return json.dumps(schema["examples"], ensure_ascii=False)
        except Exception:
            return str(schema["examples"])
    return ""


def extract_constraints(schema: Dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return ""
    out = []
    for key in (
        "pattern", "enum", "minimum", "maximum", "exclusiveMinimum",
        "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
        "minItems", "maxItems", "uniqueItems",
    ):
        if key in schema:
            value = schema[key]
            if key == "enum" and isinstance(value, list):
                out.append(f"enum: {', '.join(map(str, value))}")
            else:
                out.append(f"{key}: {value}")
    return "; ".join(out)


# ----------------------------------------------------
# --- oneOf / anyOf / allOf merging & expansion ------
# ----------------------------------------------------

def merge_schemas(base: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    """Merge 2 schema objects shallowly."""
    merged = dict(base)

    # Merge properties
    if "properties" in other and isinstance(other["properties"], dict):
        merged.setdefault("properties", {})
        merged["properties"].update(other["properties"])

    # Merge required list
    if "required" in other and isinstance(other["required"], list):
        merged.setdefault("required", [])
        for r in other["required"]:
            if r not in merged["required"]:
                merged["required"].append(r)

    # Type override only if missing
    if "type" not in merged and "type" in other:
        merged["type"] = other["type"]

    return merged


def expand_combinators(doc: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge oneOf/allOf/anyOf branches into a single schema."""
    if not isinstance(schema, dict):
        return schema

    out = dict(schema)

    for comb in ("oneOf", "anyOf", "allOf"):
        branches = schema.get(comb)
        if isinstance(branches, list):
            merged = {}
            for branch in branches:
                # Resolve $ref
                if isinstance(branch, dict) and "$ref" in branch:
                    branch = resolve_ref(doc, branch["$ref"])
                branch = expand_combinators(doc, branch)
                merged = merge_schemas(merged, branch)
            out = merge_schemas(out, merged)
            out.pop(comb, None)

    return out


# ----------------------------------------------------
# -------------- Flatten Properties ------------------
# ----------------------------------------------------

def flatten_properties(
    doc: Dict[str, Any],
    schema: Dict[str, Any],
    parent_name: str,
    location: str,
    path_context: str,
    required: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:

    rows: List[Dict[str, Any]] = []
    if not isinstance(schema, dict):
        return rows

    schema = expand_combinators(doc, schema)

    required = required or schema.get("required", []) or []
    props = schema.get("properties", {})

    if not isinstance(props, dict):
        return rows

    for name, prop_schema in props.items():
        if isinstance(prop_schema, dict) and "$ref" in prop_schema:
            prop_schema = resolve_ref(doc, prop_schema["$ref"])
        prop_schema = expand_combinators(doc, prop_schema)

        element = f"{parent_name}.{name}" if parent_name else name

        rows.append({
            "Element": element,
            "Definition": prop_schema.get("description", ""),
            "Type": normalise_type(prop_schema),
            "Example/Constraints": safe_join(
                extract_example(prop_schema),
                extract_constraints(prop_schema),
                "required" if name in required else ""
            ),
            "Location": location,
            "Path": path_context,
        })

        # Recurse into nested object
        if prop_schema.get("type") == "object" or prop_schema.get("properties"):
            rows.extend(flatten_properties(
                doc, prop_schema, element, location, path_context
            ))

        # Array of objects
        if prop_schema.get("type") == "array":
            items = prop_schema.get("items", {})
            if isinstance(items, dict) and "$ref" in items:
                items = resolve_ref(doc, items["$ref"])
            items = expand_combinators(doc, items)
            if items.get("type") == "object" or items.get("properties"):
                rows.extend(flatten_properties(
                    doc, items, f"{element}[]", location, path_context
                ))

    return rows


# ----------------------------------------------------
# ------------- Extract parameters -------------------
# ----------------------------------------------------

def extract_parameters(doc, path, method, operation):
    rows = []

    op_params = operation.get("parameters", []) or []
    path_params = (doc.get("paths", {}).get(path, {}) or {}).get("parameters", []) or []

    for p in path_params + op_params:
        if isinstance(p, dict) and "$ref" in p:
            p = resolve_ref(doc, p["$ref"])
        if not isinstance(p, dict):
            continue

        schema = p.get("schema", {}) or {}
        if isinstance(schema, dict) and "$ref" in schema:
            schema = resolve_ref(doc, schema["$ref"])
        schema = expand_combinators(doc, schema)

        rows.append({
            "Element": p.get("name", ""),
            "Definition": p.get("description", ""),
            "Type": normalise_type(schema),
            "Example/Constraints": safe_join(
                extract_example(schema),
                extract_constraints(schema),
                "required" if p.get("required") else ""
            ),
            "Location": f"parameter ({p.get('in','')})",
            "Path": f"{method.upper()} {path}",
        })

    # requestBody
    rb = operation.get("requestBody")
    if isinstance(rb, dict):
        rb_desc = rb.get("description", "")
        for media, content in (rb.get("content") or {}).items():
            schema = content.get("schema", {}) or {}
            if isinstance(schema, dict) and "$ref" in schema:
                schema = resolve_ref(doc, schema["$ref"])
            schema = expand_combinators(doc, schema)

            rows.append({
                "Element": f"requestBody ({media})",
                "Definition": rb_desc,
                "Type": normalise_type(schema),
                "Example/Constraints": safe_join(
                    extract_example(schema),
                    extract_constraints(schema)
                ),
                "Location": "requestBody",
                "Path": f"{method.upper()} {path}",
            })

            if schema.get("type") == "object":
                rows.extend(flatten_properties(
                    doc, schema, "", "requestBody", f"{method.upper()} {path}"
                ))

            if schema.get("type") == "array":
                items = schema.get("items", {})
                if isinstance(items, dict) and "$ref" in items:
                    items = resolve_ref(doc, items["$ref"])
                items = expand_combinators(doc, items)
                rows.extend(flatten_properties(
                    doc, items, "items[]", "requestBody", f"{method.upper()} {path}"
                ))

    return rows


# ----------------------------------------------------
# ------------- Extract responses --------------------
# ----------------------------------------------------

def extract_responses(doc, path, method, operation):
    rows = []
    responses = operation.get("responses", {}) or {}

    for status, resp in responses.items():
        if isinstance(resp, dict) and "$ref" in resp:
            resp = resolve_ref(doc, resp["$ref"])
        if not isinstance(resp, dict):
            continue

        desc = resp.get("description", "")
        content = resp.get("content", {}) or {}

        for media, mt in content.items():
            schema = mt.get("schema", {}) or {}
            if isinstance(schema, dict) and "$ref" in schema:
                schema = resolve_ref(doc, schema["$ref"])
            schema = expand_combinators(doc, schema)

            rows.append({
                "Element": f"response ({status}, {media})",
                "Definition": desc,
                "Type": normalise_type(schema),
                "Example/Constraints": safe_join(
                    extract_example(schema),
                    extract_constraints(schema)
                ),
                "Location": "response body",
                "Path": f"{method.upper()} {path}",
            })

            if schema.get("type") == "object":
                rows.extend(flatten_properties(
                    doc, schema, "", "response body", f"{method.upper()} {path}"
                ))

            if schema.get("type") == "array":
                items = schema.get("items", {})
                if isinstance(items, dict) and "$ref" in items:
                    items = resolve_ref(doc, items["$ref"])
                items = expand_combinators(doc, items)
                rows.extend(flatten_properties(
                    doc, items, "items[]", "response body", f"{method.upper()} {path}"
                ))

    return rows


# ----------------------------------------------------
# ------------- Extract component schemas ------------
# ----------------------------------------------------

def extract_components_schemas(doc):
    rows = []
    comps = doc.get("components", {}) or {}
    schemas = comps.get("schemas", {}) or {}

    for name, schema in schemas.items():
        if isinstance(schema, dict) and "$ref" in schema:
            schema = resolve_ref(doc, schema["$ref"])
        schema = expand_combinators(doc, schema)

        rows.append({
            "Element": name,
            "Definition": schema.get("description", ""),
            "Type": normalise_type(schema),
            "Example/Constraints": safe_join(
                extract_example(schema),
                extract_constraints(schema)
            ),
            "Location": "component schema",
            "Path": "",
        })

        rows.extend(flatten_properties(
            doc, schema, name, "component schema", ""
        ))

    return rows


# ----------------------------------------------------
# ---------------- Build dictionary ------------------
# ----------------------------------------------------

def build_dictionary(doc):
    rows = []

    paths = doc.get("paths", {}) or {}
    for path, path_item in paths.items():
        for method in ("get", "put", "post", "delete", "patch", "head", "options", "trace"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            rows.extend(extract_parameters(doc, path, method, operation))
            rows.extend(extract_responses(doc, path, method, operation))

    rows.extend(extract_components_schemas(doc))

    # Preserve all occurrences by using id(r)
    unique = []
    seen = set()
    for r in rows:
        key = (
            r["Element"],
            r["Location"],
            r["Path"],
            r["Definition"],
            r["Type"],
            r["Example/Constraints"],
            id(r)
        )
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


# ----------------------------------------------------
# ------------------- Excel / CSV --------------------
# ----------------------------------------------------

def leaf_element_from_path(element: str) -> str:
    if not element:
        return ""
    last = element.split(".")[-1].strip()
    return re.sub(r"\[\]$", "", last)


def to_excel_and_csv(rows: List[Dict[str, Any]], out_path: str, write_csv: bool):
    df = pd.DataFrame(rows, columns=[
        "Element", "Definition", "Type",
        "Example/Constraints", "Location", "Path"
    ])

    df.insert(0, "Leaf Element", df["Element"].apply(leaf_element_from_path))

    out_dir = Path(out_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Data Dictionary", index=False)

        ws = writer.sheets["Data Dictionary"]
        for col in ws.columns:
            max_len = 0
            for cell in col:
                try:
                    max_len = max(max_len, len(str(cell.value)))
                except Exception:
                    pass
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 80)

    csv_path = None
    if write_csv:
        csv_path = str(Path(out_path).with_suffix(".csv"))
        df.to_csv(csv_path, index=False, encoding="utf-8")

    return out_path, csv_path


# ----------------------------------------------------
# ---------------------- CLI -------------------------
# ----------------------------------------------------

def resolve_paths_with_dir(dir_arg, source_arg, out_arg):
    base = Path(dir_arg or ".").resolve()
    if re.match(r"^https?://", source_arg, flags=re.I):
        source = source_arg
    else:
        p = Path(source_arg)
        source = str(p if p.is_absolute() else (base / p))

    out = Path(out_arg)
    out = str(out if out.is_absolute() else (base / out))
    return source, out


def main():
    parser = argparse.ArgumentParser(
        description="Generate a data dictionary spreadsheet from an OpenAPI 3.x document."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", default="data_dictionary.xlsx")
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--dir", default=".")
    args = parser.parse_args()

    source_path, out_path = resolve_paths_with_dir(args.dir, args.source, args.out)

    try:
        doc = load_oas3(source_path)
    except Exception as ex:
        print(f"Failed to load OpenAPI document: {ex}", file=sys.stderr)
        sys.exit(1)

    rows = build_dictionary(doc)
    out_xlsx, out_csv = to_excel_and_csv(rows, out_path, args.csv)

    print(f"✔ Data dictionary written to: {out_xlsx}")
    if out_csv:
        print(f"✔ CSV written to: {out_csv}")


if __name__ == "__main__":
    main()
