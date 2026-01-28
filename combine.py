#!/usr/bin/env python3
"""
Concatenate all .xlsx files in a given folder into a single Excel file.

What it does:
- Finds every .xlsx file in the specified directory (non-recursive by default)
- Reads ALL sheets from each file
- Concatenates rows into one DataFrame (aligning columns by name)
- Adds __source_file and __source_sheet columns
- Writes a single output file: 'combined.xlsx' (or a custom path)

Options:
- Use --dir to specify the folder (defaults to current working directory)
- Use --recursive to include subfolders
- Use --output to set the output filename/path
- Use --strict-columns to enforce identical columns across sheets/files
"""

import os
import glob
import argparse
import pandas as pd
from typing import List

# ===== Defaults (can be overridden by CLI) =====
DEFAULT_OUTPUT_FILE = "combined.xlsx"
DEFAULT_RECURSIVE = False
DEFAULT_STRICT_COLUMNS = False
ENGINE = "openpyxl"  # Excel engine for reading


def list_excel_files(
    base_dir: str, pattern: str = "*.xlsx", recursive: bool = False
) -> List[str]:
    """
    List .xlsx files under base_dir, optionally recursively.
    Returns absolute paths for robustness.
    """
    search_pattern = (
        os.path.join(base_dir, "**", pattern)
        if recursive
        else os.path.join(base_dir, pattern)
    )
    files = glob.glob(search_pattern, recursive=recursive)
    # Normalize to absolute paths
    return [os.path.abspath(p) for p in files if os.path.isfile(p)]


def read_all_sheets(xlsx_path: str) -> list:
    """Read all sheets from an xlsx file and return list of DataFrames with provenance columns."""
    dfs = []
    try:
        xls = pd.ExcelFile(xlsx_path, engine=ENGINE)
        for sheet in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=sheet)
                # Skip completely empty frames
                if df is None or df.empty:
                    continue
                # Add provenance
                df["__source_file"] = os.path.basename(xlsx_path)
                df["__source_sheet"] = sheet
                dfs.append(df)
            except Exception as sheet_err:
                print(
                    f"[WARN] Failed reading sheet '{sheet}' in '{xlsx_path}': {sheet_err}"
                )
    except Exception as file_err:
        print(f"[WARN] Failed opening '{xlsx_path}': {file_err}")
    return dfs


def main():
    parser = argparse.ArgumentParser(
        description="Concatenate all .xlsx files in a folder (optionally recursive) into a single Excel file."
    )
    parser.add_argument(
        "--dir",
        "-d",
        default=".",
        help="Folder to scan for .xlsx files (default: current directory).",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Include subfolders when searching for .xlsx files.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output Excel file path (default: 'combined.xlsx' in the specified directory).",
    )
    parser.add_argument(
        "--strict-columns",
        action="store_true",
        help="Require identical columns across all inputs (excluding provenance).",
    )
    args = parser.parse_args()

    base_dir = os.path.abspath(args.dir)
    if not os.path.isdir(base_dir):
        print(f"[ERROR] '{args.dir}' is not a directory or does not exist.")
        return

    recursive = bool(args.recursive) or DEFAULT_RECURSIVE
    strict_columns = bool(args.strict_columns) or DEFAULT_STRICT_COLUMNS
    output_file = (
        args.output if args.output else os.path.join(base_dir, DEFAULT_OUTPUT_FILE)
    )
    output_file = os.path.abspath(output_file)

    print(f"Scanning directory: {base_dir}")
    print(f"Recursive: {'Yes' if recursive else 'No'}")
    print(f"Output file: {output_file}")
    print("Step 1: locating .xlsx files...")

    files = list_excel_files(base_dir=base_dir, recursive=recursive)

    # Avoid self-inclusion: if the output file already exists within the search set, exclude it
    files = [f for f in files if os.path.abspath(f) != output_file]

    if not files:
        print("No .xlsx files found in the specified folder.")
        return

    print(f"Found {len(files)} .xlsx file(s). Reading and concatenating…")

    all_frames = []
    for f in files:
        frames = read_all_sheets(f)
        if frames:
            all_frames.extend(frames)

    if not all_frames:
        print("No non-empty sheets were read—nothing to concatenate.")
        return

    # If STRICT_COLUMNS is enabled, enforce identical set of columns across all frames (excluding provenance)
    if strict_columns:
        # Identify the baseline columns from the first DataFrame (excluding provenance)
        base_cols = [c for c in all_frames[0].columns if not c.startswith("__source_")]
        aligned_frames = []
        for df in all_frames:
            # Rebuild with base columns order; missing columns become NA; extra columns are dropped
            rebuilt = pd.DataFrame(columns=base_cols)
            for c in base_cols:
                rebuilt[c] = df[c] if c in df.columns else pd.NA
            # Preserve provenance
            for p in ["__source_file", "__source_sheet"]:
                rebuilt[p] = df[p] if p in df.columns else pd.NA
            aligned_frames.append(rebuilt)
        combined = pd.concat(aligned_frames, ignore_index=True)
    else:
        # Let pandas align on column names; missing columns become NaN
        combined = pd.concat(all_frames, ignore_index=True)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # Write output
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        combined.to_excel(writer, index=False, sheet_name="combined")

    print(f"✅ Done. Combined workbook written to '{output_file}'.")


if __name__ == "__main__":
    main()
