#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xml_import.py  --  OFAC SDN XML -> SQL Server importer

Fixed schema (all tables in the target database):

  Core
  ----
  sdnEntry            One row per sanctions entry (uid is PK)

  List tables (one row per item; each has its own PK)
  ----------------------------------------------------
  programList         Sanction programs  (synthetic _RowID PK)
  akaList             Aliases            (uid PK)
  addressList         Physical addresses (uid PK)
  idList              Identity documents (uid PK)
  dateOfBirthList     Dates of birth     (uid PK)
  placeOfBirthList    Places of birth    (uid PK)
  nationalityList     Nationalities      (uid PK)
  citizenshipList     Citizenships       (uid PK)
  vesselInfo          Vessel fields      (synthetic _RowID PK)

  Many-to-many junction tables (sdnEntry <-> each list table)
  -----------------------------------------------------------
  sdnEntry_programList
  sdnEntry_akaList
  sdnEntry_addressList
  sdnEntry_idList
  sdnEntry_dateOfBirthList
  sdnEntry_placeOfBirthList
  sdnEntry_nationalityList
  sdnEntry_citizenshipList
  sdnEntry_vesselInfo

Usage
-----
    python xml_import.py --xml <path.xml> [options]

Options
-------
  --xml          Path to XML file                          (required)
  --server       SQL Server instance                       (default: .)
  --database     Target database                           (default: SDN)
  --db-schema    SQL schema name                           (default: dbo)
  --connection   Full ODBC connection string (overrides --server/--database)
  --schema-only  Print DDL to stdout only; do not connect or import
  --data-only    Skip CREATE TABLE; tables must already exist
  --drop         DROP existing tables before CREATE TABLE
  --dry-run      Print DDL to stdout AND show row counts (no DB writes)
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET

try:
    import pyodbc
except ImportError:
    pyodbc = None


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------

_NS_RE = re.compile(r"^\{(.+?)\}")


def _strip_ns(tag: str) -> str:
    return _NS_RE.sub("", tag)


def _get_ns(element: ET.Element) -> str:
    m = _NS_RE.match(element.tag)
    return f"{{{m.group(1)}}}" if m else ""


# ---------------------------------------------------------------------------
# DDL  (table creation + FK constraints)
# ---------------------------------------------------------------------------

# Tables created in dependency order (parents before children).
_DDL_TABLES = """\
CREATE TABLE [{s}].[sdnEntry] (
    [uid]       INT            NOT NULL,
    [firstName] NVARCHAR(255)  NULL,
    [lastName]  NVARCHAR(900)  NULL,
    [title]     NVARCHAR(MAX)  NULL,
    [sdnType]   NVARCHAR(50)   NULL,
    [remarks]   NVARCHAR(MAX)  NULL,
    CONSTRAINT [PK_sdnEntry] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[programList] (
    [_RowID]  INT           NOT NULL,
    [program] NVARCHAR(255) NULL,
    CONSTRAINT [PK_programList] PRIMARY KEY ([_RowID])
);
GO

CREATE TABLE [{s}].[akaList] (
    [uid]       INT           NOT NULL,
    [type]      NVARCHAR(50)  NULL,
    [category]  NVARCHAR(50)  NULL,
    [firstName] NVARCHAR(255) NULL,
    [lastName]  NVARCHAR(900) NULL,
    [title]     NVARCHAR(MAX) NULL,
    CONSTRAINT [PK_akaList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[addressList] (
    [uid]                INT           NOT NULL,
    [address1]           NVARCHAR(500) NULL,
    [address1_nm]        NVARCHAR(500) NULL,
    [address2]           NVARCHAR(500) NULL,
    [address2_nm]        NVARCHAR(500) NULL,
    [address3]           NVARCHAR(500) NULL,
    [address3_nm]        NVARCHAR(500) NULL,
    [city]               NVARCHAR(255) NULL,
    [city_nm]            NVARCHAR(255) NULL,
    [stateOrProvince]    NVARCHAR(255) NULL,
    [stateOrProvince_nm] NVARCHAR(255) NULL,
    [postalCode]         NVARCHAR(50)  NULL,
    [postalCode_nm]      NVARCHAR(50)  NULL,
    [country]            NVARCHAR(255) NULL,
    [country_nm]         NVARCHAR(255) NULL,
    CONSTRAINT [PK_addressList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[idList] (
    [uid]            INT           NOT NULL,
    [idType]         NVARCHAR(255) NULL,
    [idNumber]       NVARCHAR(900) NULL,
    [idCountry]      NVARCHAR(255) NULL,
    [issueDate]      NVARCHAR(50)  NULL,
    [expirationDate] NVARCHAR(50)  NULL,
    CONSTRAINT [PK_idList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[dateOfBirthList] (
    [uid]         INT          NOT NULL,
    [dateOfBirth] NVARCHAR(50) NULL,
    [mainEntry]   BIT          NULL,
    CONSTRAINT [PK_dateOfBirthList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[placeOfBirthList] (
    [uid]          INT           NOT NULL,
    [placeOfBirth] NVARCHAR(500) NULL,
    [mainEntry]    BIT           NULL,
    CONSTRAINT [PK_placeOfBirthList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[nationalityList] (
    [uid]       INT           NOT NULL,
    [country]   NVARCHAR(255) NULL,
    [mainEntry] BIT           NULL,
    CONSTRAINT [PK_nationalityList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[citizenshipList] (
    [uid]       INT           NOT NULL,
    [country]   NVARCHAR(255) NULL,
    [mainEntry] BIT           NULL,
    CONSTRAINT [PK_citizenshipList] PRIMARY KEY ([uid])
);
GO

CREATE TABLE [{s}].[vesselInfo] (
    [_RowID]                INT           NOT NULL,
    [callSign]              NVARCHAR(50)  NULL,
    [vesselType]            NVARCHAR(100) NULL,
    [vesselFlag]            NVARCHAR(100) NULL,
    [vesselOwner]           NVARCHAR(500) NULL,
    [tonnage]               INT           NULL,
    [grossRegisteredTonnage] INT          NULL,
    CONSTRAINT [PK_vesselInfo] PRIMARY KEY ([_RowID])
);
GO

CREATE TABLE [{s}].[sdnEntry_programList] (
    [sdnEntry_uid]      INT NOT NULL,
    [programList_RowID] INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_programList] PRIMARY KEY ([sdnEntry_uid], [programList_RowID])
);
GO

CREATE TABLE [{s}].[sdnEntry_akaList] (
    [sdnEntry_uid] INT NOT NULL,
    [akaList_uid]  INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_akaList] PRIMARY KEY ([sdnEntry_uid], [akaList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_addressList] (
    [sdnEntry_uid]    INT NOT NULL,
    [addressList_uid] INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_addressList] PRIMARY KEY ([sdnEntry_uid], [addressList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_idList] (
    [sdnEntry_uid] INT NOT NULL,
    [idList_uid]   INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_idList] PRIMARY KEY ([sdnEntry_uid], [idList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_dateOfBirthList] (
    [sdnEntry_uid]        INT NOT NULL,
    [dateOfBirthList_uid] INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_dateOfBirthList] PRIMARY KEY ([sdnEntry_uid], [dateOfBirthList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_placeOfBirthList] (
    [sdnEntry_uid]         INT NOT NULL,
    [placeOfBirthList_uid] INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_placeOfBirthList] PRIMARY KEY ([sdnEntry_uid], [placeOfBirthList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_nationalityList] (
    [sdnEntry_uid]        INT NOT NULL,
    [nationalityList_uid] INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_nationalityList] PRIMARY KEY ([sdnEntry_uid], [nationalityList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_citizenshipList] (
    [sdnEntry_uid]         INT NOT NULL,
    [citizenshipList_uid]  INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_citizenshipList] PRIMARY KEY ([sdnEntry_uid], [citizenshipList_uid])
);
GO

CREATE TABLE [{s}].[sdnEntry_vesselInfo] (
    [sdnEntry_uid]     INT NOT NULL,
    [vesselInfo_RowID] INT NOT NULL,
    CONSTRAINT [PK_sdnEntry_vesselInfo] PRIMARY KEY ([sdnEntry_uid], [vesselInfo_RowID])
);
GO
"""

_DDL_FKS = """\
ALTER TABLE [{s}].[sdnEntry_programList]
    ADD CONSTRAINT [FK_sdnEntry_programList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_programList]
    ADD CONSTRAINT [FK_sdnEntry_programList_list]
    FOREIGN KEY ([programList_RowID]) REFERENCES [{s}].[programList] ([_RowID]);
GO
ALTER TABLE [{s}].[sdnEntry_akaList]
    ADD CONSTRAINT [FK_sdnEntry_akaList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_akaList]
    ADD CONSTRAINT [FK_sdnEntry_akaList_list]
    FOREIGN KEY ([akaList_uid]) REFERENCES [{s}].[akaList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_addressList]
    ADD CONSTRAINT [FK_sdnEntry_addressList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_addressList]
    ADD CONSTRAINT [FK_sdnEntry_addressList_list]
    FOREIGN KEY ([addressList_uid]) REFERENCES [{s}].[addressList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_idList]
    ADD CONSTRAINT [FK_sdnEntry_idList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_idList]
    ADD CONSTRAINT [FK_sdnEntry_idList_list]
    FOREIGN KEY ([idList_uid]) REFERENCES [{s}].[idList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_dateOfBirthList]
    ADD CONSTRAINT [FK_sdnEntry_dateOfBirthList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_dateOfBirthList]
    ADD CONSTRAINT [FK_sdnEntry_dateOfBirthList_list]
    FOREIGN KEY ([dateOfBirthList_uid]) REFERENCES [{s}].[dateOfBirthList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_placeOfBirthList]
    ADD CONSTRAINT [FK_sdnEntry_placeOfBirthList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_placeOfBirthList]
    ADD CONSTRAINT [FK_sdnEntry_placeOfBirthList_list]
    FOREIGN KEY ([placeOfBirthList_uid]) REFERENCES [{s}].[placeOfBirthList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_nationalityList]
    ADD CONSTRAINT [FK_sdnEntry_nationalityList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_nationalityList]
    ADD CONSTRAINT [FK_sdnEntry_nationalityList_list]
    FOREIGN KEY ([nationalityList_uid]) REFERENCES [{s}].[nationalityList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_citizenshipList]
    ADD CONSTRAINT [FK_sdnEntry_citizenshipList_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_citizenshipList]
    ADD CONSTRAINT [FK_sdnEntry_citizenshipList_list]
    FOREIGN KEY ([citizenshipList_uid]) REFERENCES [{s}].[citizenshipList] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_vesselInfo]
    ADD CONSTRAINT [FK_sdnEntry_vesselInfo_entry]
    FOREIGN KEY ([sdnEntry_uid]) REFERENCES [{s}].[sdnEntry] ([uid]);
GO
ALTER TABLE [{s}].[sdnEntry_vesselInfo]
    ADD CONSTRAINT [FK_sdnEntry_vesselInfo_list]
    FOREIGN KEY ([vesselInfo_RowID]) REFERENCES [{s}].[vesselInfo] ([_RowID]);
GO
"""

# Drop in reverse dependency order (junction tables first, then list tables, then sdnEntry)
_DROP_ORDER = [
    "sdnEntry_programList", "sdnEntry_akaList", "sdnEntry_addressList",
    "sdnEntry_idList", "sdnEntry_dateOfBirthList", "sdnEntry_placeOfBirthList",
    "sdnEntry_nationalityList", "sdnEntry_citizenshipList", "sdnEntry_vesselInfo",
    "programList", "akaList", "addressList", "idList",
    "dateOfBirthList", "placeOfBirthList", "nationalityList", "citizenshipList",
    "vesselInfo", "sdnEntry",
]

# Insert in dependency order (sdnEntry + list tables before junction tables)
_INSERT_ORDER = [
    "sdnEntry",
    "programList", "akaList", "addressList", "idList",
    "dateOfBirthList", "placeOfBirthList", "nationalityList", "citizenshipList",
    "vesselInfo",
    "sdnEntry_programList", "sdnEntry_akaList", "sdnEntry_addressList",
    "sdnEntry_idList", "sdnEntry_dateOfBirthList", "sdnEntry_placeOfBirthList",
    "sdnEntry_nationalityList", "sdnEntry_citizenshipList", "sdnEntry_vesselInfo",
]

_TABLE_COLS = {
    "sdnEntry":                  ["uid", "firstName", "lastName", "title", "sdnType", "remarks"],
    "programList":               ["_RowID", "program"],
    "akaList":                   ["uid", "type", "category", "firstName", "lastName", "title"],
    "addressList":               ["uid",
                                  "address1", "address1_nm",
                                  "address2", "address2_nm",
                                  "address3", "address3_nm",
                                  "city", "city_nm",
                                  "stateOrProvince", "stateOrProvince_nm",
                                  "postalCode", "postalCode_nm",
                                  "country", "country_nm"],
    "idList":                    ["uid", "idType", "idNumber", "idCountry", "issueDate", "expirationDate"],
    "dateOfBirthList":           ["uid", "dateOfBirth", "mainEntry"],
    "placeOfBirthList":          ["uid", "placeOfBirth", "mainEntry"],
    "nationalityList":           ["uid", "country", "mainEntry"],
    "citizenshipList":           ["uid", "country", "mainEntry"],
    "vesselInfo":                ["_RowID", "callSign", "vesselType", "vesselFlag",
                                  "vesselOwner", "tonnage", "grossRegisteredTonnage"],
    "sdnEntry_programList":      ["sdnEntry_uid", "programList_RowID"],
    "sdnEntry_akaList":          ["sdnEntry_uid", "akaList_uid"],
    "sdnEntry_addressList":      ["sdnEntry_uid", "addressList_uid"],
    "sdnEntry_idList":           ["sdnEntry_uid", "idList_uid"],
    "sdnEntry_dateOfBirthList":  ["sdnEntry_uid", "dateOfBirthList_uid"],
    "sdnEntry_placeOfBirthList": ["sdnEntry_uid", "placeOfBirthList_uid"],
    "sdnEntry_nationalityList":  ["sdnEntry_uid", "nationalityList_uid"],
    "sdnEntry_citizenshipList":  ["sdnEntry_uid", "citizenshipList_uid"],
    "sdnEntry_vesselInfo":       ["sdnEntry_uid", "vesselInfo_RowID"],
}


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------

def _drop_ddl(schema: str) -> str:
    parts = []
    for tname in _DROP_ORDER:
        parts.append(
            f"IF OBJECT_ID(N'[{schema}].[{tname}]', N'U') IS NOT NULL\n"
            f"    DROP TABLE [{schema}].[{tname}];\nGO\n"
        )
    return "\n".join(parts)


def _full_ddl(schema: str, drop: bool = False) -> str:
    parts = []
    if drop:
        parts.append(_drop_ddl(schema))
    parts.append(_DDL_TABLES.replace("{s}", schema))
    parts.append(_DDL_FKS.replace("{s}", schema))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------

def _make_finders(ns: str):
    """Return (find, findall, text) functions pre-bound to the XML namespace."""

    def find(el, tag):
        return el.find(f"{ns}{tag}") if ns else el.find(tag)

    def findall(el, tag):
        return el.findall(f"{ns}{tag}") if ns else el.findall(tag)

    def text(el, tag) -> str | None:
        child = find(el, tag)
        if child is None:
            return None
        t = (child.text or "").strip()
        return t if t else None

    return find, findall, text


_STRIP_RE = re.compile(r"[\s\W]+", re.UNICODE)


def _nm(v: str | None) -> str | None:
    """Remove all spaces and punctuation from a string value."""
    if v is None:
        return None
    result = _STRIP_RE.sub("", v)
    return result if result else None


def _as_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _as_bit(v) -> int | None:
    if v is None:
        return None
    return 1 if str(v).lower() in ("true", "1", "yes") else 0


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect(root: ET.Element) -> dict[str, list]:
    """Walk the SDN XML tree and return a dict of table_name -> list of row tuples."""
    ns = _get_ns(root)
    find, findall, text = _make_finders(ns)

    rows: dict[str, list] = {t: [] for t in _INSERT_ORDER}
    prog_rowid = 0
    vessel_rowid = 0

    for entry in findall(root, "sdnEntry"):
        uid_str = text(entry, "uid")
        if not uid_str:
            continue
        uid = int(uid_str)

        rows["sdnEntry"].append((
            uid,
            text(entry, "firstName"),
            text(entry, "lastName"),
            text(entry, "title"),
            text(entry, "sdnType"),
            text(entry, "remarks"),
        ))

        # programList  --------------------------------------------------------
        prog_list = find(entry, "programList")
        if prog_list is not None:
            for prog_el in findall(prog_list, "program"):
                val = (prog_el.text or "").strip() or None
                prog_rowid += 1
                rows["programList"].append((prog_rowid, val))
                rows["sdnEntry_programList"].append((uid, prog_rowid))

        # akaList  ------------------------------------------------------------
        aka_list = find(entry, "akaList")
        if aka_list is not None:
            for aka in findall(aka_list, "aka"):
                aka_uid = _as_int(text(aka, "uid"))
                if aka_uid is None:
                    continue
                rows["akaList"].append((
                    aka_uid,
                    text(aka, "type"),
                    text(aka, "category"),
                    text(aka, "firstName"),
                    text(aka, "lastName"),
                    text(aka, "title"),
                ))
                rows["sdnEntry_akaList"].append((uid, aka_uid))

        # addressList  --------------------------------------------------------
        addr_list = find(entry, "addressList")
        if addr_list is not None:
            for addr in findall(addr_list, "address"):
                addr_uid = _as_int(text(addr, "uid"))
                if addr_uid is None:
                    continue
                a1 = text(addr, "address1")
                a2 = text(addr, "address2")
                a3 = text(addr, "address3")
                ci = text(addr, "city")
                sp = text(addr, "stateOrProvince")
                pc = text(addr, "postalCode")
                co = text(addr, "country")
                rows["addressList"].append((
                    addr_uid,
                    a1, _nm(a1),
                    a2, _nm(a2),
                    a3, _nm(a3),
                    ci, _nm(ci),
                    sp, _nm(sp),
                    pc, _nm(pc),
                    co, _nm(co),
                ))
                rows["sdnEntry_addressList"].append((uid, addr_uid))

        # idList  -------------------------------------------------------------
        id_list = find(entry, "idList")
        if id_list is not None:
            for id_el in findall(id_list, "id"):
                id_uid = _as_int(text(id_el, "uid"))
                if id_uid is None:
                    continue
                rows["idList"].append((
                    id_uid,
                    text(id_el, "idType"),
                    text(id_el, "idNumber"),
                    text(id_el, "idCountry"),
                    text(id_el, "issueDate"),
                    text(id_el, "expirationDate"),
                ))
                rows["sdnEntry_idList"].append((uid, id_uid))

        # dateOfBirthList  ----------------------------------------------------
        dob_list = find(entry, "dateOfBirthList")
        if dob_list is not None:
            for dob in findall(dob_list, "dateOfBirthItem"):
                dob_uid = _as_int(text(dob, "uid"))
                if dob_uid is None:
                    continue
                rows["dateOfBirthList"].append((
                    dob_uid,
                    text(dob, "dateOfBirth"),
                    _as_bit(text(dob, "mainEntry")),
                ))
                rows["sdnEntry_dateOfBirthList"].append((uid, dob_uid))

        # placeOfBirthList  ---------------------------------------------------
        pob_list = find(entry, "placeOfBirthList")
        if pob_list is not None:
            for pob in findall(pob_list, "placeOfBirthItem"):
                pob_uid = _as_int(text(pob, "uid"))
                if pob_uid is None:
                    continue
                rows["placeOfBirthList"].append((
                    pob_uid,
                    text(pob, "placeOfBirth"),
                    _as_bit(text(pob, "mainEntry")),
                ))
                rows["sdnEntry_placeOfBirthList"].append((uid, pob_uid))

        # nationalityList  ----------------------------------------------------
        nat_list = find(entry, "nationalityList")
        if nat_list is not None:
            for nat in findall(nat_list, "nationality"):
                nat_uid = _as_int(text(nat, "uid"))
                if nat_uid is None:
                    continue
                rows["nationalityList"].append((
                    nat_uid,
                    text(nat, "country"),
                    _as_bit(text(nat, "mainEntry")),
                ))
                rows["sdnEntry_nationalityList"].append((uid, nat_uid))

        # citizenshipList  ----------------------------------------------------
        cit_list = find(entry, "citizenshipList")
        if cit_list is not None:
            for cit in findall(cit_list, "citizenship"):
                cit_uid = _as_int(text(cit, "uid"))
                if cit_uid is None:
                    continue
                rows["citizenshipList"].append((
                    cit_uid,
                    text(cit, "country"),
                    _as_bit(text(cit, "mainEntry")),
                ))
                rows["sdnEntry_citizenshipList"].append((uid, cit_uid))

        # vesselInfo  ---------------------------------------------------------
        vessel = find(entry, "vesselInfo")
        if vessel is not None:
            vessel_rowid += 1
            rows["vesselInfo"].append((
                vessel_rowid,
                text(vessel, "callSign"),
                text(vessel, "vesselType"),
                text(vessel, "vesselFlag"),
                text(vessel, "vesselOwner"),
                _as_int(text(vessel, "tonnage")),
                _as_int(text(vessel, "grossRegisteredTonnage")),
            ))
            rows["sdnEntry_vesselInfo"].append((uid, vessel_rowid))

    return rows


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def build_conn_str(server: str, database: str) -> str:
    return (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
    )


def execute_ddl_block(cursor, ddl: str):
    """Execute GO-delimited DDL statements."""
    for stmt in re.split(r"\bGO\b", ddl, flags=re.IGNORECASE):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            cursor.execute(stmt)
        except Exception as exc:
            first_word = stmt.split()[0].upper() if stmt else ""
            if first_word in ("CREATE", "DROP"):
                raise RuntimeError(f"DDL failed:\n{stmt}\n\nError: {exc}") from exc
            print(f"  DDL warning: {exc}")


def insert_all(cursor, rows: dict[str, list], schema: str,
               batch_size: int = 500, dry_run: bool = False):
    for tname in _INSERT_ORDER:
        trows = rows.get(tname, [])
        if not trows:
            continue
        cols = _TABLE_COLS[tname]
        col_clause   = ", ".join(f"[{c}]" for c in cols)
        placeholders = ", ".join("?" * len(cols))
        sql = f"INSERT INTO [{schema}].[{tname}] ({col_clause}) VALUES ({placeholders})"
        if not dry_run:
            _executemany_safe(cursor, sql, trows, tname, cols, batch_size)
        print(f"  {tname:<45} {len(trows):>7} rows")


def _executemany_safe(cursor, sql, rows, tname, col_names, batch_size):
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        try:
            cursor.executemany(sql, batch)
        except Exception as bulk_err:
            for row_idx, row in enumerate(batch, start=i):
                try:
                    cursor.execute(sql, row)
                except Exception as row_err:
                    details = []
                    for col, val in zip(col_names, row):
                        vstr = repr(val)
                        if isinstance(val, str):
                            vstr += f"  [len={len(val)}]"
                        details.append(f"    {col}: {vstr}")
                    raise RuntimeError(
                        f"\nInsert failed in [{tname}] at row {row_idx}.\n"
                        f"SQL: {sql}\n"
                        f"Values:\n" + "\n".join(details) +
                        f"\nBatch error: {bulk_err}\n"
                        f"Row error  : {row_err}"
                    ) from row_err


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="OFAC SDN XML -> SQL Server importer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--xml",         required=True,       help="Path to XML file")
    ap.add_argument("--server",      default=".",         help="SQL Server instance (default: .)")
    ap.add_argument("--database",    default="SDN",       help="Target database (default: SDN)")
    ap.add_argument("--db-schema",   default="dbo",       help="SQL schema (default: dbo)")
    ap.add_argument("--connection",  default="",          help="Full ODBC connection string")
    ap.add_argument("--schema-only", action="store_true", help="Print DDL only, no import")
    ap.add_argument("--data-only",   action="store_true", help="Skip CREATE TABLE")
    ap.add_argument("--drop",        action="store_true", help="DROP tables before CREATE")
    ap.add_argument("--dry-run",     action="store_true", help="Print DDL; show row counts only")
    args = ap.parse_args()

    s = args.db_schema

    print(f"Parsing {args.xml} ...")
    try:
        tree = ET.parse(args.xml)
    except (ET.ParseError, OSError) as exc:
        sys.exit(f"Error reading XML: {exc}")

    root = tree.getroot()
    ddl  = _full_ddl(s, drop=args.drop)

    if args.schema_only:
        print("\n" + "=" * 72 + "\n")
        print(ddl)
        return

    print("Collecting rows from XML...")
    rows = collect(root)
    total = sum(len(v) for v in rows.values())
    print(f"  {total} total rows across {len([v for v in rows.values() if v])} tables")

    if args.dry_run:
        print("\n" + "=" * 72 + "\n")
        print(ddl)
        print("=" * 72)
        print("\n[dry-run] Row counts that would be inserted:")
        insert_all(None, rows, s, dry_run=True)
        return

    if pyodbc is None:
        sys.exit("pyodbc not installed.  Run: pip install pyodbc")

    conn_str = args.connection or build_conn_str(args.server, args.database)
    print(f"\nConnecting to [{args.server}].[{args.database}]...")
    with pyodbc.connect(conn_str) as conn:
        cursor = conn.cursor()

        if not args.data_only:
            print("Creating schema...")
            execute_ddl_block(cursor, ddl)
            conn.commit()
            print("  Schema ready.")

        print("Inserting rows...")
        insert_all(cursor, rows, s)
        conn.commit()
        print("\nComplete.")


if __name__ == "__main__":
    main()
