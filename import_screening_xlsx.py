#!/usr/bin/env python3
"""
import_screening_xlsx.py  --  Import a Delaware OFAC List xlsx into ScreeningInput

Parses the "As of" date from the header rows, then bulk-inserts all entity/
contact rows into [dbo].[ScreeningInput] in the target SQL database.

Column mapping from xlsx:
  Col B  (Name / entity org)        -> ORG_NAME
  Col E  (Contact: First Name)      -> FIRST_NAME
  Col F  (Contact: Last Name)       -> LAST_NAME
  Col H  (Contact: Mailing Address) -> ADDRESS1 / COUNTRY

File selection
--------------
  --input PATH   Use a specific local file.
  (omit)         Scan the blob container for the most recent
                 "Delaware OFAC List-*.xlsx" and download it automatically.
                 Requires STORAGE_CONNECTION_STRING env var.

Usage
-----
  # Local file
  python import_screening_xlsx.py --input "Delaware OFAC List-2026-05-19-11-02-46.xlsx" \\
      --server myserver.database.windows.net --database SDN [--drop]

  # Auto-discover from blob (Azure pipeline)
  python import_screening_xlsx.py \\
      --server myserver.database.windows.net --database SDN --drop

Environment variables:
  SQL_SERVER                Azure SQL logical server FQDN
  SQL_USER                  SQL login name
  SQL_PASSWORD              SQL password
  STORAGE_CONNECTION_STRING Required when --input is omitted
"""
import argparse
import os
import re
import sys
import tempfile
from datetime import datetime

import openpyxl
import pyodbc

# ---------------------------------------------------------------------------
# Layout constants  (1-indexed column numbers)
# ---------------------------------------------------------------------------
_DATA_START = 12     # Row where entity data begins
_AS_OF_ROW  = 3      # Row containing "As of <datetime> ..."
_AS_OF_COL  = 2      # Column B
_ORG_COL    = 2      # Column B  -- entity / org name
_FNAME_COL  = 5      # Column E  -- Contact: First Name
_LNAME_COL  = 6      # Column F  -- Contact: Last Name
_ADDR_COL   = 8      # Column H  -- Contact: Mailing Address

# Filename pattern: Delaware OFAC List-2026-05-19-11-02-46.xlsx
_BLOB_NAME_RE = re.compile(
    r'^Delaware OFAC List-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.xlsx$',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------

def _find_latest_blob(container_client) -> str | None:
    """Return the blob name of the most recent Delaware OFAC List-*.xlsx, or None."""
    candidates = []
    for blob in container_client.list_blobs():
        m = _BLOB_NAME_RE.match(blob.name)
        if m:
            dt = datetime.strptime(m.group(1), '%Y-%m-%d-%H-%M-%S')
            candidates.append((dt, blob.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _download_blob(container_client, blob_name: str, dest_path: str) -> None:
    blob_client = container_client.get_blob_client(blob_name)
    with open(dest_path, 'wb') as f:
        blob_client.download_blob().readinto(f)


# ---------------------------------------------------------------------------
# "As of" date parsing
# ---------------------------------------------------------------------------
_AS_OF_RE = re.compile(
    r'As\s+of\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',
    re.IGNORECASE,
)


def _parse_as_of(ws) -> datetime | None:
    cell_val = ws.cell(_AS_OF_ROW, _AS_OF_COL).value or ''
    m = _AS_OF_RE.search(str(cell_val))
    if m:
        return datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
    return None


# ---------------------------------------------------------------------------
# Address parsing
# Splits known country names off the end of the raw address string.
# Everything else goes into ADDRESS1 so the normaliser in sdn_match_v2 can
# still find city / state words even without structured columns.
# ---------------------------------------------------------------------------
_COUNTRIES = [
    'United States', 'United Kingdom', 'Canada', 'Singapore',
    'Guernsey', 'Jersey', 'Cayman Islands', 'British Virgin Islands',
    'Germany', 'France', 'Netherlands', 'Luxembourg', 'Switzerland',
    'Australia', 'Japan', 'China', 'Hong Kong', 'Ireland',
]


def _parse_address(raw: str) -> dict:
    if not raw:
        return {}
    addr = re.sub(r'[\r\n]+', ' ', raw).strip()
    addr = re.sub(r'\s{2,}', ' ', addr)

    if addr in _COUNTRIES:
        return {'COUNTRY': addr}

    country = None
    for ctry in _COUNTRIES:
        if addr.endswith(ctry):
            country = ctry
            addr = addr[: -len(ctry)].rstrip(', ')
            break

    return {'ADDRESS1': addr or None, 'COUNTRY': country}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description='Import Delaware OFAC List xlsx into ScreeningInput'
    )
    ap.add_argument('--input',     default=None, metavar='PATH',
                    help='Local xlsx file. Omit to auto-discover from blob storage.')
    ap.add_argument('--container', default=os.environ.get('STORAGE_CONTAINER', 'sdn'),
                    metavar='CONTAINER',
                    help='Blob container to scan (default: sdn or STORAGE_CONTAINER env var)')
    ap.add_argument('--server',   default=os.environ.get('SQL_SERVER'),
                    metavar='FQDN',   help='Azure SQL server FQDN (or SQL_SERVER env var)')
    ap.add_argument('--database', default='SDN', metavar='DB',
                    help='Target database (default: SDN)')
    ap.add_argument('--schema',   default='dbo', metavar='SCHEMA')
    ap.add_argument('--user',     default=os.environ.get('SQL_USER'),
                    metavar='USER',   help='SQL login (or SQL_USER env var)')
    ap.add_argument('--password', default=os.environ.get('SQL_PASSWORD'),
                    metavar='PWD',    help='SQL password (or SQL_PASSWORD env var)')
    ap.add_argument('--drop', action='store_true',
                    help='TRUNCATE ScreeningInput before importing')
    args = ap.parse_args()

    for attr, env in [('server', 'SQL_SERVER'), ('user', 'SQL_USER'), ('password', 'SQL_PASSWORD')]:
        if not getattr(args, attr):
            sys.exit(f'ERROR: --{attr.replace("_", "-")} not set (or {env} env var missing)')

    # ---- Resolve input file --------------------------------------------------
    tmp_path = None
    if args.input:
        xlsx_path = args.input
    else:
        conn_str = os.environ.get('STORAGE_CONNECTION_STRING')
        if not conn_str:
            sys.exit('ERROR: --input not provided and STORAGE_CONNECTION_STRING is not set.')
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            sys.exit('ERROR: azure-storage-blob not installed.')

        print(f'Scanning container "{args.container}" for Delaware OFAC List-*.xlsx...',
              flush=True)
        container_client = BlobServiceClient.from_connection_string(conn_str) \
                                            .get_container_client(args.container)
        blob_name = _find_latest_blob(container_client)
        if not blob_name:
            print(f'  No matching file found in "{args.container}". '
                  'ScreeningInput unchanged — skipping import.')
            return

        print(f'  Found: {blob_name}', flush=True)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
        os.close(tmp_fd)
        print(f'  Downloading...', flush=True)
        _download_blob(container_client, blob_name, tmp_path)
        xlsx_path = tmp_path

    # ---- Load workbook -------------------------------------------------------
    try:
        print(f'Loading {xlsx_path}...', flush=True)
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        ws = wb.active

        as_of = _parse_as_of(ws)
        if as_of:
            print(f'  As of: {as_of}', flush=True)
        else:
            print('  WARNING: Could not parse "As of" date — Upload_Date will be NULL')

        # ---- Parse rows ------------------------------------------------------
        rows = []
        for raw_row in ws.iter_rows(min_row=_DATA_START, values_only=True):
            org_name   = raw_row[_ORG_COL   - 1]
            first_name = raw_row[_FNAME_COL - 1]
            last_name  = raw_row[_LNAME_COL - 1]
            addr_raw   = raw_row[_ADDR_COL  - 1]

            if not org_name and not first_name and not last_name:
                continue

            addr = _parse_address(str(addr_raw) if addr_raw else '')
            rows.append({
                'Upload_Date': as_of,
                'ORG_NAME':    str(org_name).strip()   if org_name   else None,
                'FIRST_NAME':  str(first_name).strip() if first_name else None,
                'LAST_NAME':   str(last_name).strip()  if last_name  else None,
                'ADDRESS1':    addr.get('ADDRESS1'),
                'COUNTRY':     addr.get('COUNTRY'),
            })

        wb.close()
        print(f'  {len(rows):,} rows parsed', flush=True)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # ---- Connect and insert --------------------------------------------------
    cs = (
        f'DRIVER={{ODBC Driver 17 for SQL Server}};'
        f'SERVER={args.server};DATABASE={args.database};'
        f'UID={args.user};PWD={args.password};'
        'Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;'
    )
    print(f'Connecting to [{args.server}].[{args.database}]...', flush=True)

    with pyodbc.connect(cs) as conn:
        cur = conn.cursor()

        exists = cur.execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA=? AND TABLE_NAME='ScreeningInput'",
            [args.schema]
        ).fetchone()
        if not exists:
            sys.exit(
                'ERROR: ScreeningInput does not exist yet.\n'
                'Run sdn_match_v2.py once (with --drop-sdn-input) to create the table first.'
            )

        if args.drop:
            cur.execute(f'TRUNCATE TABLE [{args.schema}].[ScreeningInput]')
            conn.commit()
            print('  ScreeningInput truncated.', flush=True)

        cur.fast_executemany = True
        sql = f"""
            INSERT INTO [{args.schema}].[ScreeningInput]
                (Upload_Date, ORG_NAME, FIRST_NAME, LAST_NAME, ADDRESS1, COUNTRY)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = [
            (r['Upload_Date'], r['ORG_NAME'], r['FIRST_NAME'],
             r['LAST_NAME'], r['ADDRESS1'], r['COUNTRY'])
            for r in rows
        ]
        cur.executemany(sql, params)
        conn.commit()

    print(f'  {len(rows):,} rows inserted into [{args.schema}].[ScreeningInput]', flush=True)


if __name__ == '__main__':
    main()
