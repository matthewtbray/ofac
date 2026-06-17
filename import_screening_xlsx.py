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

Usage
-----
  python import_screening_xlsx.py --input "Delaware OFAC List-2026-05-19-11-02-46.xlsx" \\
      --server myserver.database.windows.net --database SDN [--drop]

Environment variables (overridden by CLI flags when provided):
  SQL_SERVER    Azure SQL logical server FQDN
  SQL_USER      SQL login name
  SQL_PASSWORD  SQL password
"""
import argparse
import os
import re
import sys
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

    # Bare country with nothing else
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
    ap.add_argument('--input',    required=True, metavar='PATH',
                    help='Path to the xlsx file')
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
            sys.exit(f'ERROR: --{attr.replace("_","-")} not set (or {env} env var missing)')

    # ---- Load workbook -------------------------------------------------------
    print(f'Loading {args.input}...', flush=True)
    wb = openpyxl.load_workbook(args.input, data_only=True)
    ws = wb.active

    as_of = _parse_as_of(ws)
    if as_of:
        print(f'  As of: {as_of}')
    else:
        print('  WARNING: Could not parse "As of" date from row 3 — Upload_Date will be NULL')

    # ---- Parse rows ----------------------------------------------------------
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

        # Verify the table exists (sdn_match_v2.py creates it on first run)
        exists = cur.execute(
            f"SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA=? AND TABLE_NAME='ScreeningInput'",
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
            print('  ScreeningInput truncated.')

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

    # Print the as-of string the user can paste into INPUT_SOURCE on the Azure job
    if as_of:
        input_source = (
            f'Delaware OFAC List — as of {as_of.strftime("%Y-%m-%d %H:%M:%S")} EST'
        )
        print()
        print('Set INPUT_SOURCE on the Container App Job before triggering:')
        print(f'  {input_source}')


if __name__ == '__main__':
    main()
