#!/usr/bin/env python3
"""
Extract ALL HTTP statuses from OAS3 JSON specs and output a summary of:
  - Status (HTTP return code)
  - ErrorCode (extracted from payload where available)
  - Description (message/reason/detail where available)
  - EnumValues (all enum values declared for the error-code property in the response schema)

Enhancements:
- --group-by-status: collapse to one row per HTTP status
- De-duplicate rows and sort
"""

import os
import sys
import json
import glob
import argparse
from typing import Dict, Any, List, Tuple, Optional

try:
    import pandas as pd
except ImportError:
    pd = None  # optional; used for CSV/XLSX fast paths

# ---- Config ----

JSON_MIME_CANDIDATES: List[str] = [
    "application/json",
    "application/problem+json",
    "application/vnd.api+json",
    "text/json",
]
CODE_KEYS = {"code", "errorCode", "error_code", "statusCode", "status_code"}
MESSAGE_KEYS = {"message", "error", "reason", "detail", "description"}

# ---- Utilities ----


def load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Could not parse JSON '{path}': {e}", file=sys.stderr)
        return None


def resolve_ref(ref: str, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not ref or not ref.startswith("#/"):
        return None
    parts = ref.lstrip("#/").split("/")
    node: Any = doc
    for p in parts:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return None
    return node if isinstance(node, dict) else None


def looks_success(status: str) -> bool:
    try:
        return 200 <= int(status) < 300
    except Exception:
        return False


def _first_string(obj: Dict[str, Any], keys: set) -> str:
    for k in keys:
        if k in obj and obj[k] is not None:
            v = obj[k]
            if isinstance(v, (str, int)):
                s = str(v)
                if s:
                    return s
    return ""


def _list_extend_unique(dst: List[str], src: List[str]):
    for x in src:
        if x not in dst:
            dst.append(x)


# ---- OpenAPI content helpers ----


def find_json_content(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = response.get("content") if isinstance(response, dict) else None
    if not isinstance(content, dict):
        return None
    for mt in JSON_MIME_CANDIDATES:
        if mt in content and isinstance(content[mt], dict):
            return content[mt]
    for _, block in content.items():
        if isinstance(block, dict) and "schema" in block:
            return block
    return None


def extract_from_examples(content: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    examples = content.get("examples")
    if isinstance(examples, dict):
        for _, ex in examples.items():
            if not isinstance(ex, dict):
                continue
            val = ex.get("value")
            if isinstance(val, dict):
                c = _first_string(val, CODE_KEYS)
                m = _first_string(val, MESSAGE_KEYS)
                if c or m:
                    pairs.append((c, m))
    schema = content.get("schema")
    if isinstance(schema, dict):
        ex = schema.get("example")
        if isinstance(ex, dict):
            c = _first_string(ex, CODE_KEYS)
            m = _first_string(ex, MESSAGE_KEYS)
            if c or m:
                pairs.append((c, m))
    return pairs


def _walk_schema_collect(
    schema: Dict[str, Any], doc: Dict[str, Any], seen: set
) -> List[Dict[str, Any]]:
    acc: List[Dict[str, Any]] = []

    def visit(s: Dict[str, Any]):
        if not isinstance(s, dict):
            return
        sid = id(s)
        if sid in seen:
            return
        seen.add(sid)
        if "$ref" in s:
            resolved = resolve_ref(s["$ref"], doc)
            if isinstance(resolved, dict):
                s = resolved
        acc.append(s)
        if s.get("type") == "array" and isinstance(s.get("items"), dict):
            visit(s["items"])
        for key in ("allOf", "oneOf", "anyOf"):
            if key in s and isinstance(s[key], list):
                for sub in s[key]:
                    if isinstance(sub, dict):
                        visit(sub)
        props = s.get("properties")
        if isinstance(props, dict):
            for p in props.values():
                if isinstance(p, dict):
                    if "$ref" in p or p.get("type") in ("array", "object"):
                        visit(p)

    visit(schema)
    return acc


def extract_codes_msgs_enums_from_schema(
    schema: Dict[str, Any], doc: Dict[str, Any]
) -> Tuple[List[str], List[str], List[str]]:
    codes: List[str] = []
    messages: List[str] = []
    enums: List[str] = []
    frags = _walk_schema_collect(schema, doc, seen=set())
    for frag in frags:
        props = frag.get("properties", {})
        if not isinstance(props, dict):
            continue
        for key, val in props.items():
            if not isinstance(val, dict):
                continue
            if key in CODE_KEYS:
                enum_vals = val.get("enum")
                if isinstance(enum_vals, list):
                    _list_extend_unique(enums, [str(x) for x in enum_vals])
                for k in ("example", "default", "const"):
                    if k in val and val[k] is not None:
                        _list_extend_unique(codes, [str(val[k])])
            if key in MESSAGE_KEYS:
                for k in ("example", "default", "description"):
                    if k in val and isinstance(val[k], str):
                        s = val[k].strip()
                        if s:
                            _list_extend_unique(messages, [s])
            if val.get("type") == "array" and isinstance(val.get("items"), dict):
                item = val["items"]
                if "$ref" in item:
                    r = resolve_ref(item["$ref"], doc)
                    if isinstance(r, dict):
                        item = r
                item_props = (
                    item.get("properties", {}) if isinstance(item, dict) else {}
                )
                if isinstance(item_props, dict):
                    c2, m2, e2 = extract_codes_msgs_enums_from_schema(
                        {"properties": item_props}, doc
                    )
                    _list_extend_unique(codes, c2)
                    _list_extend_unique(messages, m2)
                    _list_extend_unique(enums, e2)
            if val.get("type") == "object" and isinstance(val.get("properties"), dict):
                c2, m2, e2 = extract_codes_msgs_enums_from_schema(val, doc)
                _list_extend_unique(codes, c2)
                _list_extend_unique(messages, m2)
                _list_extend_unique(enums, e2)
        sch_desc = frag.get("description")
        if isinstance(sch_desc, str):
            s = sch_desc.strip()
            if s:
                _list_extend_unique(messages, [s])
    return codes, messages, enums


def extract_from_response(
    response: Dict[str, Any], doc: Dict[str, Any]
) -> Tuple[List[Tuple[str, str]], List[str]]:
    pairs: List[Tuple[str, str]] = []
    enums: List[str] = []
    content = find_json_content(response)
    if not content:
        return pairs, enums
    pairs.extend(extract_from_examples(content))
    schema = content.get("schema")
    if isinstance(schema, dict):
        codes, messages, enum_vals = extract_codes_msgs_enums_from_schema(schema, doc)
        _list_extend_unique(enums, enum_vals)
        if codes and messages:
            if len(messages) == len(codes):
                pairs.extend(list(zip(codes, messages)))
            else:
                default_msg = messages[0] if messages else ""
                for c in codes:
                    pairs.append((c, default_msg))
                for m in messages[1:]:
                    pairs.append(("", m))
        elif codes and not messages:
            for c in codes:
                pairs.append((c, ""))
        elif messages and not codes:
            for m in messages:
                pairs.append(("", m))
    # de-dup inside response
    seen = set()
    uniq = []
    for c, m in pairs:
        k = (c or "", m or "")
        if k not in seen:
            seen.add(k)
            uniq.append((c, m))
    enums = sorted(set(enums), key=lambda x: (len(x), x))
    return uniq, enums


# ---- Traversal ----


def walk_paths(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    paths = doc.get("paths", {})
    if not isinstance(paths, dict):
        return rows
    for _, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in (
            "get",
            "post",
            "put",
            "patch",
            "delete",
            "options",
            "head",
            "trace",
        ):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            responses = op.get("responses", {})
            if not isinstance(responses, dict):
                continue
            for status, resp in responses.items():
                if isinstance(resp, dict) and "$ref" in resp:
                    r = resolve_ref(resp["$ref"], doc)
                    if r:
                        resp = r
                if not isinstance(resp, dict):
                    continue
                pairs, enums = extract_from_response(resp, doc)
                if not pairs:
                    rows.append(
                        {
                            "Status": status,
                            "ErrorCode": "",
                            "Description": "Success"
                            if looks_success(status)
                            else "No detail found in schema/examples",
                            "EnumValues": ", ".join(enums) if enums else "",
                        }
                    )
                else:
                    for code, desc in pairs:
                        rows.append(
                            {
                                "Status": status,
                                "ErrorCode": str(code) if code is not None else "",
                                "Description": str(desc) if desc is not None else "",
                                "EnumValues": ", ".join(enums) if enums else "",
                            }
                        )
    return rows


def scan_folder(folder: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in glob.glob(os.path.join(folder, "**", "*.json"), recursive=True):
        doc = load_json(path)
        if not doc or "openapi" not in doc:
            continue
        rows.extend(walk_paths(doc))
    return rows


# ---- Aggregation ----


def dedupe_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    uniq: List[Dict[str, str]] = []
    for r in rows:
        key = (
            r.get("Status", ""),
            r.get("ErrorCode", ""),
            r.get("Description", ""),
            r.get("EnumValues", ""),
        )
        if key not in seen:
            seen.add(key)
            uniq.append(
                {
                    "Status": key[0],
                    "ErrorCode": key[1],
                    "Description": key[2],
                    "EnumValues": key[3],
                }
            )
    return uniq


def sort_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    def sort_key(r: Dict[str, str]) -> Tuple[int, str, str, str]:
        status = r.get("Status", "")
        try:
            status_num = int(status)
        except:
            status_num = 10**9
        return (status_num, status, r.get("ErrorCode", ""), r.get("Description", ""))

    return sorted(rows, key=sort_key)


def group_by_status(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Collapse to one row per status:
      - ErrorCode: comma-separated unique codes
      - Description: semicolon-separated unique descriptions
      - EnumValues: comma-separated unique enum values
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s = r.get("Status", "")
        b = buckets.setdefault(s, {"codes": set(), "descs": set(), "enums": set()})
        if r.get("ErrorCode"):
            b["codes"].add(r["ErrorCode"])
        if r.get("Description"):
            b["descs"].add(r["Description"])
        if r.get("EnumValues"):
            for v in [x.strip() for x in r["EnumValues"].split(",") if x.strip()]:
                b["enums"].add(v)
    out: List[Dict[str, str]] = []
    for s, agg in buckets.items():
        # sort codes/enums numerically when possible, then lexicographically
        def smart_sort(values: List[str]) -> List[str]:
            nums, strs = [], []
            for v in values:
                try:
                    nums.append(int(v))
                except:
                    strs.append(v)
            return [str(n) for n in sorted(nums)] + sorted(strs)

        codes = smart_sort(list(agg["codes"]))
        enums = smart_sort(list(agg["enums"]))
        descs = sorted(agg["descs"])
        out.append(
            {
                "Status": s,
                "ErrorCode": ", ".join(codes),
                "Description": "; ".join(descs),
                "EnumValues": ", ".join(enums),
            }
        )
    # final sort by status
    return sort_rows(out)


# ---- Output ----


def to_markdown(rows: List[Dict[str, str]]) -> str:
    if not rows:
        return "# No data found\n"
    header = (
        "| Status | Error Code | Description | EnumValues |\n"
        "|--------|------------|-------------|------------|\n"
    )
    lines: List[str] = []
    for r in rows:
        desc = (r.get("Description", "") or "").replace("|", "\\|")
        enums = (r.get("EnumValues", "") or "").replace("|", "\\|")
        lines.append(
            f"| {r.get('Status','')} | {r.get('ErrorCode','')} | {desc} | {enums} |"
        )
    return header + "\n".join(lines) + "\n"


def write_output(rows: List[Dict[str, str]], fmt: str, outfile: str):
    fmt = fmt.lower()
    if fmt == "md":
        md = to_markdown(rows)
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[INFO] Markdown written to: {outfile}")
    elif fmt == "csv":
        try:
            if pd is not None:
                df = pd.DataFrame(rows)
                df.to_csv(outfile, index=False)
            else:
                raise ImportError("pandas not available")
        except Exception:
            import csv

            with open(outfile, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["Status", "ErrorCode", "Description", "EnumValues"]
                )
                writer.writeheader()
                writer.writerows(rows)
        print(f"[INFO] CSV written to: {outfile}")
    elif fmt == "xlsx":
        if pd is None:
            raise RuntimeError(
                "pandas with openpyxl is required for xlsx output.\nInstall with: pip install pandas openpyxl"
            )
        df = pd.DataFrame(rows)
        df.to_excel(outfile, index=False, engine="openpyxl")
        print(f"[INFO] Excel written to: {outfile}")
    else:
        raise ValueError(f"Unsupported output format: {fmt}")


# ---- Main ----


def main():
    ap = argparse.ArgumentParser(
        description="Extract Status, ErrorCode, Description, EnumValues from OAS3 JSON specs."
    )
    ap.add_argument(
        "folder", help="Folder containing .json OpenAPI 3 specs (recursively scanned)."
    )
    ap.add_argument(
        "--out", choices=["md", "csv", "xlsx"], default="md", help="Output format"
    )
    ap.add_argument(
        "--outfile", default="http_error_codes_table.md", help="Output file name"
    )
    ap.add_argument(
        "--group-by-status",
        action="store_true",
        help="Summarise to one row per HTTP status",
    )
    args = ap.parse_args()

    raw_rows = scan_folder(args.folder)
    if not raw_rows:
        print(
            "[WARN] No rows produced. Check that your folder contains valid OAS3 JSON specs."
        )

    # always de-dup first
    rows = dedupe_rows(raw_rows)

    # optionally group by status
    if args.group_by_status:
        rows = group_by_status(rows)
    else:
        rows = sort_rows(rows)

    write_output(rows, args.out, args.outfile)


if __name__ == "__main__":
    main()
