#!/usr/bin/env python3
"""
export_results.py  --  Export SDN matching results to CSV files

Reads from SDNReporting and writes one CSV per match table filtered to a
single Run_ID. Excludes all _NoMatch tables and the Summary tables.
Also writes a MatchingResults_RunLog.txt summary file.

In blob mode, archives SDN.XML from the container root into the run folder
and deletes the source copy after a successful copy.

Output modes
------------
  --output-path PATH   Write to a local folder (good for testing)
  --output-blob        Write to Azure Blob Storage

Usage examples
--------------
Local test:
  python export_results.py --run-id 1 ^
    --out-server . --out-database SDNReporting ^
    --output-path C:\\logs

Azure Blob:
  python export_results.py --run-id 1 ^
    --out-server myserver.database.windows.net --out-database SDNReporting ^
    --output-blob

Environment variables
---------------------
  SQL_USER                  Azure SQL login username
  SQL_PASSWORD              Azure SQL login password
  SQL_DRIVER                ODBC driver name (default: ODBC Driver 17 for SQL Server)
  STORAGE_CONNECTION_STRING Azure Storage connection string (required for --output-blob)
"""

import argparse
import csv
import io
import os
import subprocess
import sys
import tempfile
from datetime import datetime

try:
    import pyodbc
except ImportError:
    sys.exit("pyodbc not installed.  Run: pip install pyodbc")

# ---------------------------------------------------------------------------
# Tables to export (Run_ID filtered).
# Excludes: Matching_Summary_*, MatchingResults_v2_RunLog (written separately)
# ---------------------------------------------------------------------------
EXPORT_TABLES = [
    'MatchingResults_Person_Full',
    'MatchingResults_AKA',
    'MatchingResults_OrgName',
    'MatchingResults_OrgName_AKA',
    'MatchingResults_Address',
    'MatchingResults_LinkedTo',
    'MatchingResults_Phone',
    'MatchingResults_Report',
]

# ---------------------------------------------------------------------------
# Tables to truncate after backup (all MatchingResults_* detail tables).
# Keeps: MatchingResults_v2_RunLog, Matching_Summary_Person,
#        Matching_Summary_Org, StreetAddressMatchType (lookup).
# MatchingResults_Report is a VIEW — nothing to truncate.
# ---------------------------------------------------------------------------
TRUNCATE_TABLES = [
    'MatchingResults_Person_Full',
    'MatchingResults_Person_NoMatch',
    'MatchingResults_AKA',
    'MatchingResults_AKA_NoMatch',
    'MatchingResults_OrgName',
    'MatchingResults_OrgName_NoMatch',
    'MatchingResults_OrgName_AKA',
    'MatchingResults_OrgName_AKA_NoMatch',
    'MatchingResults_Address',
    'MatchingResults_Address_NoMatch',
    'MatchingResults_LinkedTo',
    'MatchingResults_LinkedTo_NoMatch',
    'MatchingResults_Phone',
    'MatchingResults_NoMatch',
]


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def _build_conn_str(server: str, database: str) -> str:
    user   = os.environ.get('SQL_USER')
    pwd    = os.environ.get('SQL_PASSWORD')
    driver = os.environ.get('SQL_DRIVER', 'ODBC Driver 17 for SQL Server')
    if user and pwd:
        return (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            f"UID={user};PWD={pwd};"
            "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
        )
    return (
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        "Trusted_Connection=yes;"
    )


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------

def _backup_database(server: str, database: str, bak_path: str) -> None:
    """BACKUP DATABASE to a local .bak file via sqlcmd subprocess.
    Uses sqlcmd rather than pyodbc because BACKUP generates STATS progress
    messages that interfere with pyodbc result-set handling.
    """
    user = os.environ.get('SQL_USER')
    pwd  = os.environ.get('SQL_PASSWORD')
    sql  = (f"BACKUP DATABASE [{database}] TO DISK = N'{bak_path}' "
            f"WITH COMPRESSION, INIT, STATS = 10;")
    cmd  = ['sqlcmd', '-S', server, '-Q', sql]
    if user and pwd:
        cmd += ['-U', user, '-P', pwd]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(
            f"BACKUP DATABASE [{database}] failed (exit {result.returncode}):\n"
            f"{result.stderr or result.stdout}\n"
            f"  Verify the SQL Server service account has write access to: "
            f"{os.path.dirname(bak_path)}"
        )


def _upload_bak(bak_path: str, bak_name: str,
                storage_cs: str, container: str, folder: str) -> None:
    """Upload a local .bak file to Azure Blob Storage."""
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        sys.exit("azure-storage-blob not installed.  Run: pip install azure-storage-blob")
    dest = f"{folder.rstrip('/')}/{bak_name}"
    svc  = BlobServiceClient.from_connection_string(storage_cs)
    with open(bak_path, 'rb') as f:
        svc.get_blob_client(container, dest).upload_blob(f, overwrite=True)
    print(f"    uploaded → {container}/{dest}")


def _export_bacpac(server: str, database: str, bacpac_path: str) -> None:
    """Export a .bacpac using SqlPackage.exe (must be on PATH or in Program Files).
    SqlPackage is installed with SSDT / SSMS or downloadable from Microsoft.
    """
    user = os.environ.get('SQL_USER')
    pwd  = os.environ.get('SQL_PASSWORD')
    cmd  = [
        'SqlPackage',
        '/Action:Export',
        f'/SourceServerName:{server}',
        f'/SourceDatabaseName:{database}',
        f'/TargetFile:{bacpac_path}',
        '/SourceEncryptConnection:True',
    ]
    if user and pwd:
        cmd += [f'/SourceUser:{user}', f'/SourcePassword:{pwd}']
    else:
        cmd += ['/SourceTrustServerCertificate:True']
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"SqlPackage failed (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout)[-1000:]}"
        )


def _truncate_results(conn, schema: str) -> None:
    """TRUNCATE all MatchingResults_* detail tables listed in TRUNCATE_TABLES.
    Skips any table that does not exist (e.g. never created, or is a view).
    """
    cur = conn.cursor()
    for table in TRUNCATE_TABLES:
        row = cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND TABLE_TYPE = 'BASE TABLE'",
            [schema, table],
        ).fetchone()
        if not row or row[0] == 0:
            print(f"  {table:<60} (not found — skipped)")
            continue
        cur.execute(f"TRUNCATE TABLE [{schema}].[{table}];")
        print(f"  {table:<60} truncated")
    conn.commit()


def _shrink_database(server: str, database: str, target_pct: int = 10) -> None:
    """Shrink transaction log to 1 MB, then shrink data files to target_pct free space.
    Works on on-premise SQL Server and Azure SQL Database.
    target_pct: percentage of free space to leave after shrink (default 10).
    """
    cs = _build_conn_str(server, database)
    with pyodbc.connect(cs, autocommit=True) as conn:
        row = conn.execute(
            "SELECT name FROM sys.database_files WHERE type_desc = 'LOG'"
        ).fetchone()
        if row:
            print(f"    shrinking log '{row[0]}' to 1 MB...")
            conn.execute(f"DBCC SHRINKFILE (N'{row[0]}', 1);")
        print(f"    shrinking data files ({target_pct}% free space target)...")
        conn.execute(f"DBCC SHRINKDATABASE (0, {target_pct});")


# ---------------------------------------------------------------------------
# Run metadata
# ---------------------------------------------------------------------------

def _get_latest_run_id(conn, schema: str) -> int:
    row = conn.cursor().execute(
        f"SELECT MAX(run_id) FROM [{schema}].[MatchingResults_v2_RunLog]"
    ).fetchone()
    if not row or row[0] is None:
        sys.exit("No runs found in MatchingResults_v2_RunLog.")
    return int(row[0])


def _get_run_info(conn, schema: str, run_id: int) -> dict:
    row = conn.cursor().execute(
        f"SELECT run_id, run_date, sdn_publish_date, input_source, "
        f"       records_checked, total_rows_written "
        f"FROM   [{schema}].[MatchingResults_v2_RunLog] "
        f"WHERE  run_id = ?",
        [run_id]
    ).fetchone()
    if not row:
        sys.exit(f"Run ID {run_id} not found in MatchingResults_v2_RunLog.")
    return {
        'run_id':            row[0],
        'run_date':          row[1],
        'sdn_publish_date':  row[2],
        'input_source':      row[3],
        'records_checked':   row[4],
        'total_rows_written': row[5],
    }


def _folder_name(run: dict) -> str:
    pub = run['sdn_publish_date']
    if pub is None:
        print("  Warning: sdn_publish_date is NULL — using today's date for folder name.")
        return datetime.now().strftime('%Y%m%d')
    if hasattr(pub, 'strftime'):
        return pub.strftime('%Y%m%d')
    return str(pub).replace('-', '')[:8]


# ---------------------------------------------------------------------------
# Output handlers
# ---------------------------------------------------------------------------

class LocalOutput:
    """Writes files to a local directory."""

    def __init__(self, base_path: str, folder: str):
        self.folder_path = os.path.join(base_path, folder)
        os.makedirs(self.folder_path, exist_ok=True)
        print(f"Output folder: {self.folder_path}")

    def write_csv(self, filename: str, headers: list, rows: list):
        path = os.path.join(self.folder_path, filename)
        with open(path, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(headers)
            csv.writer(f).writerows(rows)
        print(f"  {filename:<60} {len(rows):>8,} rows")

    def write_text(self, filename: str, content: str):
        path = os.path.join(self.folder_path, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  {filename}")

    def copy_sdn_xml(self, *_):
        print("  (SDN.XML copy/delete skipped in local output mode)")


class BlobOutput:
    """Writes files to Azure Blob Storage under logs/{folder}/."""

    def __init__(self, folder: str, conn_str: str, container: str):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            sys.exit("azure-storage-blob not installed.  Run: pip install azure-storage-blob")
        self._svc       = BlobServiceClient.from_connection_string(conn_str)
        self._container = container
        self._prefix    = f"logs/{folder}/"
        print(f"Blob output: {container}/{self._prefix}")

    def _blob(self, filename: str):
        return self._svc.get_blob_client(self._container, f"{self._prefix}{filename}")

    def write_csv(self, filename: str, headers: list, rows: list):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerows(rows)
        self._blob(filename).upload_blob(buf.getvalue().encode('utf-8'), overwrite=True)
        print(f"  {filename:<60} {len(rows):>8,} rows")

    def write_text(self, filename: str, content: str):
        self._blob(filename).upload_blob(content.encode('utf-8'), overwrite=True)
        print(f"  {filename}")

    def copy_sdn_xml(self, container: str):
        src_client = self._svc.get_blob_client(container, 'SDN.XML')
        dst_client = self._blob('SDN.XML')
        try:
            dst_client.start_copy_from_url(src_client.url)
            # Wait for copy to complete
            props = dst_client.get_blob_properties()
            if props.copy.status == 'success':
                src_client.delete_blob()
                print(f"  SDN.XML archived to {self._prefix}SDN.XML and deleted from root.")
            else:
                print(f"  WARNING: SDN.XML copy status={props.copy.status} — source NOT deleted.")
        except Exception as exc:
            print(f"  WARNING: SDN.XML copy failed: {exc} — source NOT deleted.")


# ---------------------------------------------------------------------------
# Export logic
# ---------------------------------------------------------------------------

def _export_table(conn, schema: str, table: str, run_id: int, output,
                  order_by: str = None):
    cur = conn.cursor()
    sql = f"SELECT * FROM [{schema}].[{table}] WHERE Run_ID = ?"
    if order_by:
        sql += f" ORDER BY {order_by}"
    try:
        cur.execute(sql, [run_id])
    except pyodbc.ProgrammingError:
        print(f"  {table:<60} (table not found — skipped)")
        return
    headers = [d[0] for d in cur.description]
    rows    = cur.fetchall()
    output.write_csv(f"{table}.csv", headers, rows)


def _runlog_text(run: dict) -> str:
    return (
        f"Run Date:            {run['run_date']}\n"
        f"Run ID:              {run['run_id']}\n"
        f"SDN Publish Date:    {run['sdn_publish_date']}\n"
        f"Input Source:        {run['input_source']}\n"
        f"Records Checked:     {run['records_checked']:,}\n"
        f"Total Rows Written:  {run['total_rows_written']:,}\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Export SDN matching results to CSV files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('--run-id',       type=int, default=None, metavar='N',
                    help='Run ID to export (default: latest run)')
    ap.add_argument('--out-server',   default='.',            metavar='SERVER')
    ap.add_argument('--out-database', default='SDNReporting', metavar='DATABASE')
    ap.add_argument('--out-schema',   default='dbo',          metavar='SCHEMA')

    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output-path', metavar='PATH',
                      help='Local folder for output files (testing)')
    mode.add_argument('--output-blob', action='store_true',
                      help='Write to Azure Blob Storage '
                           '(requires STORAGE_CONNECTION_STRING env var)')

    ap.add_argument('--storage-container', default='sdn', metavar='CONTAINER',
                    help='Blob container name (default: sdn)')

    bkp = ap.add_argument_group('backup (optional, on-premise SQL Server only)')
    bkp.add_argument('--sdn-server',       default=None, metavar='SERVER',
                     help='SQL Server hosting the SDN database '
                          '(default: same as --out-server)')
    bkp.add_argument('--sdn-database',     default='SDN', metavar='DATABASE',
                     help='SDN database name (default: SDN)')
    bkp.add_argument('--backup-path',      default=None, metavar='PATH',
                     help='Local folder to write .bak files '
                          '(e.g. C:\\backups)')
    bkp.add_argument('--backup-blob',      action='store_true',
                     help='Upload .bak files to Azure Blob Storage '
                          '(requires STORAGE_CONNECTION_STRING; '
                          'backs up to temp file first, then uploads)')
    bkp.add_argument('--backup-container', default=None, metavar='CONTAINER',
                     help='Blob container for .bak files '
                          '(default: same as --storage-container)')
    bkp.add_argument('--backup-folder',    default='backups', metavar='FOLDER',
                     help='Blob folder prefix for backup files (default: backups)')

    bkp.add_argument('--bacpac-path',      default=None, metavar='PATH',
                     help='Local folder to write .bacpac files '
                          '(Azure SQL; requires SqlPackage.exe on PATH)')
    bkp.add_argument('--bacpac-blob',      action='store_true',
                     help='Upload .bacpac files to Azure Blob Storage '
                          '(requires STORAGE_CONNECTION_STRING and SqlPackage.exe)')

    cln = ap.add_argument_group('cleanup (runs after backup)')
    cln.add_argument('--truncate',         action='store_true',
                     help='Truncate all MatchingResults_* detail tables after backup '
                          '(keeps Matching_Summary_* and RunLog)')
    cln.add_argument('--shrink',           action='store_true',
                     help='Shrink log and data files after truncate '
                          '(on-premise SQL Server; Azure SQL manages its own storage)')
    cln.add_argument('--shrink-target',    type=int, default=10, metavar='PCT',
                     help='Target free-space %% after shrink (default: 10)')
    args = ap.parse_args()

    # Apply defaults that depend on other arg values
    if args.sdn_server is None:
        args.sdn_server = args.out_server
    if args.backup_container is None:
        args.backup_container = args.storage_container

    cs = _build_conn_str(args.out_server, args.out_database)
    with pyodbc.connect(cs) as conn:
        s = args.out_schema

        run_id = args.run_id if args.run_id is not None else _get_latest_run_id(conn, s)
        print(f"Exporting Run_ID={run_id} from [{args.out_server}].[{args.out_database}]")

        run    = _get_run_info(conn, s, run_id)
        folder = _folder_name(run)
        print(f"SDN publish date: {run['sdn_publish_date']}  →  folder: {folder}")

        if args.output_blob:
            storage_cs = os.environ.get('STORAGE_CONNECTION_STRING')
            if not storage_cs:
                sys.exit("STORAGE_CONNECTION_STRING environment variable not set.")
            output = BlobOutput(folder, storage_cs, args.storage_container)
        else:
            output = LocalOutput(args.output_path, folder)

        print("\nExporting tables...")
        for table in EXPORT_TABLES:
            order = 'Input_Record_ID, Match_Type' if table == 'MatchingResults_Report' else None
            _export_table(conn, s, table, run_id, output, order_by=order)

        print("\nWriting run log...")
        output.write_text('MatchingResults_RunLog.txt', _runlog_text(run))

        if args.output_blob:
            print("\nArchiving SDN.XML...")
            output.copy_sdn_xml(args.storage_container)

    print("\nExport complete.")

    date_str    = _folder_name(run)                    # YYYYMMDD from sdn_publish_date (for export folder)
    backup_date = datetime.now().strftime('%Y%m%d')    # today's date (for backup filenames)
    storage_cs  = os.environ.get('STORAGE_CONNECTION_STRING')

    # -----------------------------------------------------------------------
    # Optional .bak backups (on-premise SQL Server)
    # -----------------------------------------------------------------------
    if args.backup_path or args.backup_blob:
        print("\nRunning .bak database backups...")

        if args.backup_blob and not storage_cs:
            print("  WARNING: STORAGE_CONNECTION_STRING not set — "
                  "blob backup skipped.")

        for svr, db in [
            (args.sdn_server,  args.sdn_database),
            (args.out_server,  args.out_database),
        ]:
            bak_name = f"{db}_{backup_date}.bak"
            tmp_file = None

            if args.backup_path:
                os.makedirs(args.backup_path, exist_ok=True)
                bak_path = os.path.join(args.backup_path, bak_name)
            else:
                tmp_file = tempfile.NamedTemporaryFile(
                    suffix='.bak', delete=False)
                tmp_file.close()
                bak_path = tmp_file.name

            try:
                print(f"  Backing up [{db}] on [{svr}]...")
                _backup_database(svr, db, bak_path)
                if args.backup_path:
                    print(f"    saved → {bak_path}")
                if args.backup_blob and storage_cs:
                    print(f"  Uploading {bak_name}...")
                    _upload_bak(bak_path, bak_name, storage_cs,
                                args.backup_container, args.backup_folder)
            finally:
                if tmp_file and os.path.exists(bak_path):
                    os.unlink(bak_path)

        print("  .bak backups complete.")

    # -----------------------------------------------------------------------
    # Optional .bacpac exports (Azure SQL Database)
    # -----------------------------------------------------------------------
    if args.bacpac_path or args.bacpac_blob:
        print("\nRunning .bacpac exports (SqlPackage)...")

        if args.bacpac_blob and not storage_cs:
            print("  WARNING: STORAGE_CONNECTION_STRING not set — "
                  "bacpac blob upload skipped.")

        for svr, db in [
            (args.sdn_server,  args.sdn_database),
            (args.out_server,  args.out_database),
        ]:
            bpac_name = f"{db}_{backup_date}.bacpac"
            tmp_file  = None

            if args.bacpac_path:
                os.makedirs(args.bacpac_path, exist_ok=True)
                bpac_path = os.path.join(args.bacpac_path, bpac_name)
            else:
                tmp_file = tempfile.NamedTemporaryFile(
                    suffix='.bacpac', delete=False)
                tmp_file.close()
                bpac_path = tmp_file.name

            try:
                print(f"  Exporting [{db}] on [{svr}] → {bpac_name}...")
                _export_bacpac(svr, db, bpac_path)
                if args.bacpac_path:
                    print(f"    saved → {bpac_path}")
                if args.bacpac_blob and storage_cs:
                    print(f"  Uploading {bpac_name}...")
                    _upload_bak(bpac_path, bpac_name, storage_cs,
                                args.backup_container, args.backup_folder)
            finally:
                if tmp_file and os.path.exists(bpac_path):
                    os.unlink(bpac_path)

        print("  .bacpac exports complete.")

    # -----------------------------------------------------------------------
    # Optional truncate + shrink (runs after all backups are confirmed done)
    # -----------------------------------------------------------------------
    if args.truncate:
        print("\nTruncating MatchingResults_* detail tables...")
        cs2 = _build_conn_str(args.out_server, args.out_database)
        with pyodbc.connect(cs2) as conn2:
            _truncate_results(conn2, args.out_schema)
        print("  Truncation complete.")

    if args.shrink:
        for svr, db in [
            (args.sdn_server,  args.sdn_database),
            (args.out_server,  args.out_database),
        ]:
            print(f"\nShrinking [{db}] on [{svr}] "
                  f"(target: {args.shrink_target}% free)...")
            _shrink_database(svr, db, args.shrink_target)
            print(f"  [{db}] shrink complete.")


if __name__ == '__main__':
    main()
