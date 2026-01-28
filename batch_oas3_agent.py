import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import List

# Paths to your scripts
AGENT_SCRIPT = "oas3_data_dictionary_agent.py"
COMBINE_SCRIPT = "combine.py"


def find_json_files(target_dir: Path) -> List[str]:
    """
    Return a list of JSON basenames present in target_dir.
    Basenames are used so the agent script can open them relative to --dir.
    """
    files: List[str] = []
    for entry in target_dir.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".json":
            files.append(entry.name)  # e.g., "spec.json"
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run OAS3 data dictionary agent for all JSON specs in a directory, then combine outputs."
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Folder containing .json files to process (default: current working directory).",
    )
    args = parser.parse_args()

    base_dir = Path(args.dir).resolve()
    if not base_dir.is_dir():
        print(f"❌ Provided --dir path is not a directory or does not exist: {base_dir}")
        sys.exit(1)

    print(f"📂 Target directory: {base_dir}")

    # Discover JSON files in the target directory
    files = find_json_files(base_dir)
    if not files:
        print("No .json files found. Exiting.")
        return

    print(f"Found {len(files)} .json file(s).")
    generated_outputs: List[str] = []
    any_failed = False

    for file in files:
        output_file = f"{Path(file).stem}_data_dictionary.xlsx"
        out_full_path = str(base_dir / output_file)

        print(f"Processing {file} → {out_full_path}")

        cmd = [
            sys.executable,  # Use the current Python interpreter
            AGENT_SCRIPT,
            "--dir",
            str(base_dir),  # Pass --dir to the agent script
            "--source",
            file,  # filename relative to --dir
            "--out",
            out_full_path,  # full output path
        ]

        try:
            subprocess.run(cmd, check=True)
            generated_outputs.append(out_full_path)
            print(f"✅ Success: {file} → {out_full_path}")
        except subprocess.CalledProcessError as e:
            any_failed = True
            print(f"❌ Failed processing {file}. Exit code: {e.returncode}")
        except FileNotFoundError:
            any_failed = True
            print(f"❌ Could not find script: {AGENT_SCRIPT}")
            break

    # Only call combine.py if all prior runs succeeded
    if not any_failed:
        print("All files processed successfully. Running combine.py…")
        try:
            combine_cmd = [
                sys.executable,
                COMBINE_SCRIPT,
                "--dir",
                str(base_dir),  # Pass --dir to combine script
            ]

            # If combine.py instead expects explicit files, you could do:
            # combine_cmd = [sys.executable, COMBINE_SCRIPT, "--dir", str(base_dir), *generated_outputs]

            subprocess.run(combine_cmd, check=True)
            print("✅ combine.py completed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"❌ combine.py failed. Exit code: {e.returncode}")
        except FileNotFoundError:
            print(f"❌ Could not find script: {COMBINE_SCRIPT}")
    else:
        print(
            "Some files failed. Skipping combine.py."
        )  # single, valid print statement


if __name__ == "__main__":
    main()
