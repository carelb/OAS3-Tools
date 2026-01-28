#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
Merge all .xlsx and .csv files in an input directory into a single Excel workbook,
one tab per file (first sheet for .xlsx). Tab names are derived from filenames,
trimmed to 31 chars and deduplicated if needed.

Requirements:
    pip install pandas openpyxl

Examples:
    python merge_dir_to_tabs.py --dir "C:\\data\\in" --out "C:\\data\\combined.xlsx"
    python merge_dir_to_tabs.py --dir ./in --out ./combined.xlsx --exclude "~$*" --limit-rows 200000

Notes on Excel sheet names:
- Max 31 chars
- Cannot contain: : \ / ? * [ ]
- Cannot be empty or only quotes
"""

import argparse
import sys
from pathlib import Path
import pandas as pd
import re


def parse_args():
    r"""
    Parse command-line arguments.

    --dir: Input directory to scan (recursively) for .xlsx and .csv files.
    --out: Output Excel file path.
    --include: Additional include globs besides *.xlsx and *.csv.
    --exclude: Exclude globs (defaults to "~$*" to skip Excel lock files).
    --encoding: Preferred CSV encoding (default utf-8; fallbacks attempted).
    --limit-rows: Optional cap on rows per file (useful for very large files).
    --verbose: Print progress messages.
    """
    ap = argparse.ArgumentParser(
        description="Combine all .xlsx and .csv in a directory into one Excel with one tab per file."
    )
    ap.add_argument(
        "--dir", required=True, type=Path, help="Input directory to scan for files."
    )
    ap.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output Excel file path (e.g., combined.xlsx).",
    )
    ap.add_argument(
        "--include",
        nargs="*",
        default=[],
        help="Additional include globs (besides *.xlsx, *.csv).",
    )
    ap.add_argument(
        "--exclude",
        nargs="*",
        default=["~$*"],
        help="Exclude globs (e.g., temp/lock files).",
    )
    ap.add_argument(
        "--encoding",
        default="utf-8",
        help="Preferred CSV encoding; will fallback if needed.",
    )
    ap.add_argument(
        "--limit-rows",
        type=int,
        default=None,
        help="Optional cap on rows read per file.",
    )
    ap.add_argument("--verbose", action="store_true", help="Print progress.")
    return ap.parse_args()


def log(msg, verbose):
    """Print a message if verbose is True."""
    if verbose:
        print(msg, flush=True)


def find_files(indir: Path, include, exclude, verbose=False):
    """Return a sorted list of files in indir matching include patterns and not matching exclude patterns."""
    if not indir.exists() or not indir.is_dir():
        raise NotADirectoryError(
            f"Input directory not found or not a directory: {indir}"
        )

    # Base includes
    patterns = ["*.xlsx", "*.csv"] + (include or [])
    files = []
    for pat in patterns:
        files.extend(indir.rglob(pat))

    # Exclusions
    excl = set()
    for ex in exclude or []:
        excl.update(indir.rglob(ex))

    final = sorted([p for p in set(files) if p.is_file() and p not in excl])
    log(f"Discovered {len(final)} files after filtering.", verbose)
    return final


def sanitise_sheet_name(name: str) -> str:
    r"""
    Clean a proposed sheet name:

    - Remove illegal characters: : \ / ? * [ ]
    - Trim quotes and whitespace
    - Enforce max 31 characters
    """
    # Replace illegal characters with underscore
    base = re.sub(r"[:\\/?*\[\]]", "_", name)
    base = base.strip().strip("'").strip()
    if not base:
        base = "Sheet"
    return base[:31]


def dedupe_sheet_name(name: str, used: set) -> str:
    """Ensure unique sheet names by appending suffixes like (2), (3)... respecting the 31-char limit."""
    if name not in used:
        used.add(name)
        return name
    i = 2
    while True:
        suffix = f" ({i})"
        trimmed = name[: 31 - len(suffix)] + suffix
        if trimmed not in used:
            used.add(trimmed)
            return trimmed
        i += 1


def read_csv_safely(
    path: Path, encoding: str, limit_rows: int | None, verbose=False
) -> pd.DataFrame:
    r"""
    Read CSV with best-effort delimiter/encoding handling.

    - Uses pandas engine='python' with sep=None to infer delimiter.
    - Tries preferred encoding first, then fallbacks: utf-8-sig, cp1252, latin1.
    """
    try:
        df = pd.read_csv(
            path,
            sep=None,  # infer delimiter
            engine="python",
            nrows=limit_rows,
            encoding=encoding,
        )
        return df
    except UnicodeDecodeError:
        for enc in ["utf-8-sig", "cp1252", "latin1"]:
            try:
                df = pd.read_csv(
                    path,
                    sep=None,
                    engine="python",
                    nrows=limit_rows,
                    encoding=enc,
                )
                return df
            except UnicodeDecodeError:
                continue
        raise
    except Exception as e:
        if verbose:
            print(f"Failed reading CSV {path}: {e}", file=sys.stderr)
        raise


def read_xlsx_first_sheet(
    path: Path, limit_rows: int | None, verbose=False
) -> pd.DataFrame:
    """Read the first sheet from an .xlsx file using openpyxl; optionally cap rows."""
    try:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl", nrows=limit_rows)
        return df
    except Exception as e:
        if verbose:
            print(f"Failed reading Excel {path}: {e}", file=sys.stderr)
        raise


def main():
    """Entry point: scan directory, read files, and write combined workbook."""
    args = parse_args()
    indir: Path = args.dir
    outpath: Path = args.out

    files = find_files(indir, args.include, args.exclude, args.verbose)
    if not files:
        print("No matching files found (.xlsx or .csv). Nothing to do.")
        sys.exit(0)

    used_sheet_names = set()
    dfs_and_names: list[tuple[pd.DataFrame, str]] = []

    for p in files:
        try:
            if p.suffix.lower() == ".csv":
                df = read_csv_safely(p, args.encoding, args.limit_rows, args.verbose)
            elif p.suffix.lower() == ".xlsx":
                df = read_xlsx_first_sheet(p, args.limit_rows, args.verbose)
            else:
                continue

            base = p.stem
            sheet_base = sanitise_sheet_name(base)
            sheetname = dedupe_sheet_name(sheet_base, used_sheet_names)

            dfs_and_names.append((df, sheetname))
            log(f"Queued: {p.name} -> tab '{sheetname}' (rows={len(df)})", args.verbose)

        except Exception as e:
            print(f"Skipping {p} due to read error: {e}", file=sys.stderr)

    if not dfs_and_names:
        print("No data frames were created from the input files. Exiting.")
        sys.exit(1)

    outpath.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(outpath, engine="openpyxl") as writer:
        for df, sheetname in dfs_and_names:
            df.to_excel(writer, index=False, sheet_name=sheetname)

    print(f"Done. Wrote combined workbook to: {outpath.resolve()}")


if __name__ == "__main__":
    main()
