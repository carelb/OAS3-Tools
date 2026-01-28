#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extract JSON Schema elements to CSV with type, restrictions, enumerations, and examples.

- Supports local $ref pointers, including array indices per RFC-6901
- Flattens allOf / anyOf / oneOf conservatively (union for enum/required; keep existing on conflicts)
- Does not invent examples; only emits example/default if present
- Compatible with JSON Schema draft-07 style documents

Usage:
  python extract_schema_to_csv.py --input schema.json --output elements.csv
  python extract_schema_to_csv.py --input schema.json --output metadata.csv --root-pointer "#/properties/metadata"
  python extract_schema_to_csv.py --dir ./artifacts --input schema.json --output elements.csv
"""

import argparse
import csv
import json
import os
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional, Set

# -----------------------------
# Path resolution helpers
# -----------------------------


def resolve_with_dir(path: str, base_dir: Optional[str]) -> str:
    """
    Resolve a path with optional base directory.
    - Expands ~ and environment vars
    - If base_dir is provided and path is relative, join base_dir/path
    - Normalize to absolute filesystem path
    """
    path = os.path.expanduser(os.path.expandvars(path))
    if os.path.isabs(path):
        return os.path.abspath(path)
    if base_dir:
        base_dir = os.path.abspath(os.path.expanduser(os.path.expandvars(base_dir)))
        path = os.path.join(base_dir, path)
    return os.path.abspath(path)


# -----------------------------
# JSON Pointer resolver (supports arrays)
# -----------------------------


def resolve_json_pointer(doc: Any, pointer: str) -> Any:
    if not pointer.startswith("#"):
        raise ValueError("Only local refs are supported. Pointer must start with '#'.")
    if pointer == "#":
        return doc
    parts = pointer.lstrip("#/").split("/")
    cur: Any = doc
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                raise KeyError("JSON pointer not found: {}".format(pointer))
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                raise KeyError(
                    "JSON pointer expected integer index for array: {} in {}".format(
                        part, pointer
                    )
                )
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                raise KeyError(
                    "JSON pointer array index out of range: {}".format(pointer)
                )
        else:
            raise KeyError(
                "JSON pointer cannot descend into non-container: {}".format(pointer)
            )
    return cur


# -----------------------------
# Constraint merge helpers
# -----------------------------

CONSTRAINT_KEYS = {
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "format",
    "enum",
    "default",
    "example",
    "const",
    "required",
}
TYPE_KEYS = {"type"}
TITLE_DESC_KEYS = {"description", "x-internal"}  # 'title' removed
PROPERTIES_KEYS = {"properties", "items", "additionalProperties"}


def merge_enums(values: List[Any]) -> Optional[List[Any]]:
    s: Set[Any] = set()
    for v in values:
        if isinstance(v, list):
            s.update(v)
    return list(s) if s else None


def conservative_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(a)
    for k, v in b.items():
        if k == "enum":
            merged = merge_enums([out.get("enum"), v])
            if merged is not None:
                out["enum"] = sorted(
                    merged, key=lambda x: json.dumps(x, ensure_ascii=False)
                )
        elif k == "required":
            ra = set(out.get("required", []) or [])
            rb = set(v or [])
            if ra or rb:
                out["required"] = sorted(ra.union(rb))
        elif k not in out:
            out[k] = v
        else:
            if out[k] != v:
                # keep existing; do not guess reconciliation
                pass
    return out


# -----------------------------
# Schema walker
# -----------------------------


class SchemaWalker:
    def __init__(self, root: Dict[str, Any]) -> None:
        self.root = root
        self.ref_cache: Dict[str, Dict[str, Any]] = {}

    def deref(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        if "$ref" not in schema:
            return schema
        ref = schema["$ref"]
        if ref in self.ref_cache:
            base = deepcopy(self.ref_cache[ref])
        else:
            target = resolve_json_pointer(self.root, ref)
            base = (
                self.deref(deepcopy(target))
                if isinstance(target, dict)
                else deepcopy(target)
            )
            self.ref_cache[ref] = deepcopy(base)
        siblings = {k: v for k, v in schema.items() if k != "$ref"}
        return conservative_merge(base, siblings)

    def flatten_composites(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        schema = self.deref(schema)
        for key in ("allOf", "anyOf", "oneOf"):
            if key in schema and isinstance(schema[key], list):
                merged: Dict[str, Any] = {}
                for sub in schema[key]:
                    sub = self.flatten_composites(self.deref(sub))
                    merged = conservative_merge(
                        merged,
                        {
                            k: v
                            for k, v in sub.items()
                            if k
                            in (
                                CONSTRAINT_KEYS
                                | TYPE_KEYS
                                | TITLE_DESC_KEYS
                                | PROPERTIES_KEYS
                            )
                        },
                    )
                schema = conservative_merge(schema, merged)
        return schema

    def iter_elements(
        self, schema: Dict[str, Any], base_path: str = ""
    ) -> List[Dict[str, Any]]:
        schema = self.flatten_composites(schema)
        records: List[Dict[str, Any]] = []
        self._walk(
            schema,
            base_path or "",
            parent_required=schema.get("required", []),
            out=records,
        )
        return records

    def _walk(
        self,
        schema: Dict[str, Any],
        path: str,
        parent_required: List[str],
        out: List[Dict[str, Any]],
    ) -> None:
        schema = self.flatten_composites(schema)

        # Record current node (skip root '')
        if path:
            out.append(self._make_record(path, schema, parent_required))

        # Objects
        props = schema.get("properties")
        addl = schema.get("additionalProperties")
        required_here = schema.get("required", [])

        if props and isinstance(props, dict):
            for name, sub in props.items():
                sub = self.deref(sub) if isinstance(sub, dict) else sub
                sub_path = f"{path}.{name}" if path else name
                self._walk(sub, sub_path, required_here, out)

        if isinstance(addl, dict):
            sub = self.deref(addl)
            sub_path = f"{path}.*" if path else "*"
            self._walk(sub, sub_path, [], out)

        # Arrays
        t = schema.get("type")
        if t == "array" or "items" in schema:
            items = schema.get("items")
            if isinstance(items, dict):
                items = self.deref(items)
                sub_path = f"{path}[]" if path else "[]"
                self._walk(items, sub_path, [], out)
            elif isinstance(items, list):  # tuple validation
                for idx, it in enumerate(items):
                    it = self.deref(it)
                    sub_path = f"{path}[{idx}]" if path else f"[{idx}]"
                    self._walk(it, sub_path, [], out)

    def _make_record(
        self, path: str, schema: Dict[str, Any], parent_required: List[str]
    ) -> Dict[str, Any]:
        def as_csv_value(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False)
            return str(v)

        description = schema.get("description", "")
        xtype = schema.get("type", "")
        xformat = schema.get("format", "")
        xenum = schema.get("enum", "")
        pattern = schema.get("pattern", "")

        # numeric
        minimum = schema.get("minimum", "")
        maximum = schema.get("maximum", "")
        exclusive_min = schema.get("exclusiveMinimum", "")
        exclusive_max = schema.get("exclusiveMaximum", "")
        multiple_of = schema.get("multipleOf", "")

        # string
        min_len = schema.get("minLength", "")
        max_len = schema.get("maxLength", "")

        # array
        min_items = schema.get("minItems", "")
        max_items = schema.get("maxItems", "")

        # required flag from parent
        this_name = path.split(".")[-1]
        if this_name.endswith("[]"):
            this_base_name = this_name[:-2]
        elif this_name.endswith(".*"):
            this_base_name = this_name[:-2]
        elif "[" in this_name:  # tuple item; no property name
            this_base_name = ""
        else:
            this_base_name = this_name
        is_required = this_base_name in (parent_required or [])

        default = schema.get("default", "")
        example = schema.get("example", "")
        x_internal = schema.get("x-internal", "")

        # Derive 'leaf element' from the terminal segment of the path,
        # normalising array/property decorations
        leaf = path.split(".")[-1] if "." in path else path
        # Normalise common forms
        if leaf.endswith("[]"):
            leaf_element = leaf[:-2] if leaf[:-2] else "[]"  # handle root '[]'
        elif leaf.endswith(".*"):
            leaf_element = "*" if leaf[:-2] == "" else leaf[:-2]
        else:
            leaf_element = leaf  # includes '[idx]' and '*' as-is

        return {
            "leaf element": as_csv_value(leaf_element),
            "path": path,
            "description": as_csv_value(description),
            "type": as_csv_value(xtype),
            "format": as_csv_value(xformat),
            "enum": as_csv_value(xenum),
            "pattern": as_csv_value(pattern),
            "min": as_csv_value(minimum),
            "max": as_csv_value(maximum),
            "exclusiveMinimum": as_csv_value(exclusive_min),
            "exclusiveMaximum": as_csv_value(exclusive_max),
            "multipleOf": as_csv_value(multiple_of),
            "minLength": as_csv_value(min_len),
            "maxLength": as_csv_value(max_len),
            "minItems": as_csv_value(min_items),
            "maxItems": as_csv_value(max_items),
            "required": "true" if is_required else "",
            "default": as_csv_value(default),
            "example": as_csv_value(example),
            "x-internal": as_csv_value(x_internal),
        }


# -----------------------------
# CSV writer
# -----------------------------

CSV_COLUMNS = [
    "leaf element",  # new first column
    "path",
    # "title",  # removed
    "description",
    "type",
    "format",
    "enum",
    "pattern",
    "min",
    "max",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "required",
    "default",
    "example",
    "x-internal",
]


def write_csv(records: List[Dict[str, Any]], output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})


# -----------------------------
# Main
# -----------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract JSON Schema elements to CSV.")
    ap.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to JSON Schema file (relative paths can be resolved with --dir)",
    )
    ap.add_argument(
        "--output",
        "-o",
        required=True,
        help="Path to output CSV file (relative paths can be resolved with --dir)",
    )
    ap.add_argument(
        "--root-pointer",
        default="#",
        help="JSON Pointer to the schema root (default: #)",
    )
    ap.add_argument(
        "--dir",
        default=None,
        help="Base directory used to resolve relative --input and --output paths",
    )
    args = ap.parse_args()

    input_path = resolve_with_dir(args.input, args.dir)
    output_path = resolve_with_dir(args.output, args.dir)

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(
            "Failed to read JSON schema '{}': {}".format(input_path, e), file=sys.stderr
        )
        sys.exit(1)

    try:
        root_schema = resolve_json_pointer(data, args.root_pointer)
    except Exception as e:
        print(
            "Failed to resolve root pointer '{}': {}".format(args.root_pointer, e),
            file=sys.stderr,
        )
        sys.exit(1)

    walker = SchemaWalker(data)
    records = walker.iter_elements(root_schema)

    if not records:
        print(
            "No elements found. Check that your JSON Schema has 'properties' or 'items'.",
            file=sys.stderr,
        )

    try:
        write_csv(records, output_path)
    except Exception as e:
        print("Failed to write CSV '{}': {}".format(output_path, e), file=sys.stderr)
        sys.exit(1)

    print("Wrote {} rows to {}".format(len(records), output_path))


if __name__ == "__main__":
    main()
