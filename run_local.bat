@ECHO OFF
SETLOCAL

REM =====================================================================
REM  run_local.bat  --  Full SDN matching pipeline (on-premise SQL Server)
REM
REM  Steps:
REM    1. Download SDN.XML from OFAC
REM    2. Import SDN.XML into SDN database
REM    3. Run SDN matching  (SDN -> SDNReporting)
REM    4. Export results, back up databases, truncate, shrink
REM =====================================================================

REM ---- Configuration --------------------------------------------------
SET PYTHON=python
SET SCRIPTS=C:\pythonscripts
SET SDN_XML=C:\sdn_data\sdn.xml
SET OUTPUT_PATH=C:\sdn_output
SET BACKUP_PATH=C:\sdn_backups

SET SDN_SERVER=.
SET SDN_DB=SDN
SET OUT_SERVER=.
SET OUT_DB=SDNReporting

REM  Leave blank to use Windows integrated auth (typical for on-premise)
SET SQL_USER=
SET SQL_PASSWORD=

REM  Number of SDN entries to evaluate: a number (e.g. 500) or ALL
SET SDN_LIMIT=600


REM =====================================================================
ECHO.
ECHO ====================================================================
ECHO  SDN Matching Pipeline  --  %DATE%  %TIME%
ECHO ====================================================================

REM ---- Step 1: Download SDN.XML from OFAC ----------------------------
ECHO.
ECHO [1/4] Downloading SDN.XML from OFAC...
IF NOT EXIST "C:\sdn_data" MKDIR "C:\sdn_data"
curl -L --silent --show-error --output "%SDN_XML%" ^
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
IF %ERRORLEVEL% NEQ 0 (
    ECHO ERROR: Failed to download SDN.XML.
    ECHO        Check internet access and OFAC URL availability.
    GOTO :FAIL
)
ECHO   Saved: %SDN_XML%

REM ---- Step 2: Import SDN.XML into SQL Server ------------------------
ECHO.
ECHO [2/4] Importing SDN.XML into [%SDN_SERVER%].[%SDN_DB%]...
%PYTHON% "%SCRIPTS%\xml_import.py" ^
    --xml      "%SDN_XML%" ^
    --server   "%SDN_SERVER%" ^
    --database "%SDN_DB%" ^
    --drop
IF %ERRORLEVEL% NEQ 0 (
    ECHO ERROR: xml_import.py failed.
    GOTO :FAIL
)

REM ---- Step 3: Run SDN matching --------------------------------------
ECHO.
ECHO [3/4] Running SDN matching...
%PYTHON% "%SCRIPTS%\sdn_match_v2.py" ^
    --input-screening ^
    --sdn-server "%SDN_SERVER%" --sdn-database "%SDN_DB%" ^
    --out-server "%OUT_SERVER%"  --out-database "%OUT_DB%" ^
    --sdn-limit %SDN_LIMIT% ^
    --no-csv
IF %ERRORLEVEL% NEQ 0 (
    ECHO ERROR: sdn_match_v2.py failed.
    GOTO :FAIL
)

REM ---- Step 4: Export, back up, truncate, shrink --------------------
ECHO.
ECHO [4/4] Exporting results / backing up / truncating / shrinking...
%PYTHON% "%SCRIPTS%\export_results.py" ^
    --out-server    "%OUT_SERVER%"  --out-database "%OUT_DB%" ^
    --sdn-server    "%SDN_SERVER%" --sdn-database "%SDN_DB%" ^
    --output-path   "%OUTPUT_PATH%" ^
    --backup-path   "%BACKUP_PATH%" ^
    --truncate ^
    --shrink
IF %ERRORLEVEL% NEQ 0 (
    ECHO ERROR: export_results.py failed.
    GOTO :FAIL
)

ECHO.
ECHO ====================================================================
ECHO  Pipeline complete  --  %DATE%  %TIME%
ECHO ====================================================================
GOTO :EOF


:FAIL
ECHO.
ECHO ====================================================================
ECHO  PIPELINE FAILED  --  %DATE%  %TIME%
ECHO  Review the output above for details.
ECHO ====================================================================
EXIT /B 1
