# OAS3 Toolkit Utilities

A collection of Python utilities to **validate, normalise, analyse, and publish** OpenAPI 3 (OAS3) specifications at scale. These tools are designed for integration governance, API estates, and CI/CD automation where consistency and evidence matter.

> **Why this toolkit?**  
> Working across many services and teams, it’s easy for API specifications to drift. These scripts provide repeatable, CLI‑first workflows to lint, standardise, extract data dictionaries, compile error catalogues, and assemble stakeholder packs.

---

## Table of Contents
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Tools](#tools)
  - [1) batch_oas3_agent.py](#1-batch_oas3_agentpy)
  - [2) combine.py](#2-combinepy)
  - [3) compile_http_errors_from_oas.py](#3-compile_http_errors_from_oaspy)
  - [4) extract_schema_to_csv.py](#4-extract_schema_to_csvpy)
  - [5) merge_dir_to_tabs.py](#5-merge_dir_to_tabspy)
  - [6) oas3_data_dictionary_agent.py](#6-oas3_data_dictionary_agentpy)
- [Common CLI Options](#common-cli-options)
- [Examples](#examples)
  - [A. Validate and normalise all specs](#a-validate-and-normalise-all-specs)
  - [B. Build an HTTP error catalogue](#b-build-an-http-error-catalogue)
  - [C. Generate a data dictionary pack](#c-generate-a-data-dictionary-pack)
  - [D. Consolidate CSV outputs into one workbook](#d-consolidate-csv-outputs-into-one-workbook)
- [Directory Layout (example)](#directory-layout-example)
- [CI/CD Hints](#cicd-hints)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features
- **Batch OAS3 processing** across repositories/folders.
- **Strict validation** with optional linting.
- **Schema extraction** to CSV/Excel data dictionaries.
- **HTTP error catalogue** generator (4xx/5xx responses).
- **Report assembly** into Excel workbooks (one sheet per file).
- **Composable utilities** that play well in CI.

## Prerequisites
- **Python**: 3.10+ (3.11+ recommended).
- **OS**: Windows, macOS, or Linux.
- **Recommended packages** (depending on scripts):
  - `pyyaml`, `jsonschema` or `openapi-spec-validator`
  - `pandas`, `openpyxl`
  - `tabulate` (optional pretty printing)

> Install as needed, e.g.:
```bash
python -m pip install pyyaml jsonschema pandas openpyxl tabulate
```

> Create and activate a virtual environment (recommended):
```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
source .venv/bin/activate
```

---

## Quick Start
1. Place your OAS3 specs (YAML/JSON) under `./specs`.
2. Run the **batch** agent to validate & normalise:
```bash
python batch_oas3_agent.py --in-dir ./specs --out-dir ./specs_clean --report ./reports/batch_oas3_summary.csv --strict
```
3. Generate a **data dictionary** for one spec:
```bash
python extract_schema_to_csv.py --spec ./specs/my-service.yaml --output ./reports/my-service_data_dictionary.csv
```
4. Build an **HTTP error** catalogue across all specs:
```bash
python compile_http_errors_from_oas.py --in-dir ./specs --output ./reports/http_errors.csv
```
5. Merge CSV reports into **one Excel workbook**:
```bash
python merge_dir_to_tabs.py --in-dir ./reports --output ./reports/pack.xlsx
```

---

## Tools

### 1) `batch_oas3_agent.py`
Batch processor for OAS3 specs. Validates, optionally lints, and writes cleaned copies with a summary report.

**How to run**
```bash
python batch_oas3_agent.py \
  --in-dir ./specs \
  --out-dir ./specs_clean \
  --report ./reports/batch_oas3_summary.csv \
  [--strict] [--fail-on-error] [--lint]
```

**Arguments**
- `--in-dir` (required): Root directory containing `.yaml/.yml/.json` specs.
- `--out-dir`: Destination for normalised specs (default: alongside inputs).
- `--report`: CSV/JSON summary of results.
- `--strict`: Treat warnings as errors.
- `--fail-on-error`: Non‑zero exit if any spec fails.
- `--lint`: Enable linting rules if available.

**Output**
- Cleaned specs in `--out-dir`.
- Summary report with per‑file status, version, and issues.

---

### 2) `combine.py`
Combine many files of the same type (CSV/JSON/text) into a single artefact.

**How to run**
```bash
python combine.py \
  --pattern "./out/**/*.csv" \
  --format csv \
  --output ./out/combined.csv \
  [--dedupe] [--sort "column1,column2"]
```

**Arguments**
- `--pattern` (required): Glob pattern for input files.
- `--format`: `csv|json|text`.
- `--output` (required): Output file path.
- `--dedupe`: Remove duplicate rows (CSV/JSON).
- `--sort`: Sort columns (CSV) or keys (JSON array of objects).

---

### 3) `compile_http_errors_from_oas.py`
Aggregate **4xx/5xx** responses across OAS3 files into a single reference (CSV/Excel).

**How to run**
```bash
python compile_http_errors_from_oas.py \
  --in-dir ./specs \
  --output ./reports/http_errors.csv \
  [--include-examples] [--xlsx]
```

**Arguments**
- `--in-dir` (required): Where to scan for specs.
- `--output` (required): CSV path (or `.xlsx` if `--xlsx`).
- `--include-examples`: Attempt to capture example payloads.
- `--xlsx`: Write Excel workbook instead of CSV.

**Output Columns (CSV)**
- `spec`, `path`, `operation`, `status`, `description`, `schema_ref`, `example` (if available)

---

### 4) `extract_schema_to_csv.py`
Flatten OAS3 component schemas into a **data dictionary** (CSV/Excel).

**How to run**
```bash
python extract_schema_to_csv.py \
  --spec ./specs/my-service.yaml \
  --output ./reports/my-service_data_dictionary.csv \
  [--xlsx] [--schema MyDomainModel]
```

**Arguments**
- `--spec` (required): Path to a single spec file.
- `--output` (required): CSV path (or `.xlsx` if `--xlsx`).
- `--schema`: Restrict to a single schema under `#/components/schemas`.
- `--xlsx`: Write Excel workbook instead of CSV.

**Output Columns (typical)**
- `schema`, `property_path`, `type`, `format`, `required`, `nullable`, `enum`, `description`, `example`

---

### 5) `merge_dir_to_tabs.py`
Merge a directory of CSVs into one **Excel workbook**, one sheet per file.

**How to run**
```bash
python merge_dir_to_tabs.py \
  --in-dir ./reports/partials \
  --output ./reports/compiled_pack.xlsx \
  [--order "overview,errors,data_dictionary"] [--auto-fit]
```

**Arguments**
- `--in-dir` (required): Directory containing `.csv` files.
- `--output` (required): Target `.xlsx` file.
- `--order`: Comma‑separated preferred sheet order (by filename stem).
- `--auto-fit`: Best‑effort column sizing.

---

### 6) `oas3_data_dictionary_agent.py`
High‑level agent that validates specs and produces **data dictionaries** (CSV/Excel) with optional enrichment.

**How to run**
```bash
python oas3_data_dictionary_agent.py \
  --in-dir ./specs \
  --out-dir ./reports/data_dictionary \
  [--enrichment ./reference/business_metadata.csv] \
  [--xlsx] [--strict]
```

**Arguments**
- `--in-dir` (required): Root folder to scan for specs.
- `--out-dir` (required): Where to write outputs.
- `--enrichment`: CSV/JSON with additional business metadata to merge.
- `--xlsx`: Emit Excel instead of CSV.
- `--strict`: Fail on warnings.

**Outputs**
- Per‑spec data dictionary files.
- An index/summary file listing all specs processed and their artefacts.

---

## Common CLI Options
- `--in-dir` / `--out-dir` — input/output roots.
- `--spec` — single spec path.
- `--output` / `--report` — result file destinations.
- `--strict` — treat warnings as errors.
- `--xlsx` — Excel output where supported.
- `--log-level` — `DEBUG|INFO|WARN|ERROR` (where available).

---

## Examples

### A. Validate and normalise all specs
```bash
python batch_oas3_agent.py \
  --in-dir ./specs \
  --out-dir ./specs_clean \
  --report ./reports/batch_oas3_summary.csv \
  --strict --fail-on-error
```

### B. Build an HTTP error catalogue
```bash
python compile_http_errors_from_oas.py \
  --in-dir ./specs \
  --output ./reports/http_errors.csv \
  --include-examples
```

### C. Generate a data dictionary pack
```bash
# 1) Extract data dictionary for each spec
python oas3_data_dictionary_agent.py \
  --in-dir ./specs \
  --out-dir ./reports/data_dictionary \
  --xlsx --strict

# 2) Merge all CSVs into one workbook
python merge_dir_to_tabs.py \
  --in-dir ./reports/data_dictionary \
  --output ./reports/data_dictionary_pack.xlsx \
  --auto-fit
```

### D. Consolidate CSV outputs into one workbook
```bash
python merge_dir_to_tabs.py \
  --in-dir ./reports \
  --output ./reports/compiled_pack.xlsx \
  --order "overview,http_errors,data_dictionary"
```

---

## Directory Layout (example)
```
/
├─ specs/
│  ├─ service-a.yaml
│  ├─ service-b.yaml
├─ reports/
│  ├─ batch_oas3_summary.csv
│  ├─ http_errors.csv
│  ├─ data_dictionary/
│  │  ├─ service-a_data_dictionary.csv
│  │  └─ service-b_data_dictionary.csv
│  └─ compiled_pack.xlsx
├─ tools/
│  ├─ batch_oas3_agent.py
│  ├─ combine.py
│  ├─ compile_http_errors_from_oas.py
│  ├─ extract_schema_to_csv.py
│  ├─ merge_dir_to_tabs.py
│  └─ oas3_data_dictionary_agent.py
└─ README.md
```

---

## CI/CD Hints
- Use a matrix to shard large spec sets by subdirectory.
- Cache Python dependencies between runs.
- Publish generated CSV/XLSX files as build artefacts.
- Enforce `--strict` in governance pipelines to stop regressions early.

**GitHub Actions (example)**
```yaml
name: OAS3 Tooling
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: python -m pip install -U pip pyyaml jsonschema pandas openpyxl tabulate
      - run: |
          python batch_oas3_agent.py --in-dir ./specs --out-dir ./specs_clean \
            --report ./reports/batch_oas3_summary.csv --strict --fail-on-error
      - uses: actions/upload-artifact@v4
        with:
          name: oas3-reports
          path: reports/**
```

---

## Troubleshooting
- **Spec not detected**: Ensure the file extension is one of `.yaml`, `.yml`, or `.json`.
- **Validation fails**: Run with `--strict` off to see warnings first; inspect the report for line/key details.
- **Excel output issues**: Verify `openpyxl` is installed and the destination path is writable.
- **Memory/large files**: Run scripts spec‑by‑spec or increase available memory for large schemas.

---

## Contributing
1. Open an issue describing the change or problem.
2. Fork and create a feature branch.
3. Add tests/samples where relevant.
4. Open a PR with a concise description and screenshots where helpful.
