echo Please delete the /dictionary folder. Continue? Press Y to proceed, N to cancel.
choice /c YN /n /m "Your choice: "
rem CHOICE sets errorlevel to 1 for Y, 2 for N
if errorlevel 2 (
    echo Cancelled.
    exit /b 1
)
echo Continuing...

md dictionary

del .\dir1-files\*.xlsx
python batch_oas3_agent.py --dir ./dir1-files
copy .\dir1-files\combined.xlsx .\dictionary\dictionary-dir1-files.xlsx

del .\dir2-files\*.xlsx
python batch_oas3_agent.py --dir ./dir2-files
copy .\dir2-files\combined.xlsx .\dictionary\dictionary-dir2-files.xlsx

python merge_dir_to_tabs.py --dir ./dictionary --out ./dictionary/combined_dictionaries.xlsx