#!/usr/bin/env python3
"""
schema_ddl.py

Creates or alters all Registrations_DW schema objects not already in place.
Safe to re-run -- all DDL is guarded with IF NOT EXISTS / IF COL_LENGTH checks.

Objects created or modified
---------------------------
  NEW TABLES
    dbo.DIM_Entity_Type
    dbo.DIM_Status
    dbo.DIM_Company
    dbo.DIM_Registration_Principal   (Type 2 SCD, individual-level)
    dbo.DIM_Company_Principal        (structure only -- populated later)
    dbo.Staging_Registration

  ALTERED TABLES
    dbo.Fact_Registration            add Original_Formation_Juris_ID, Company_ID
                                     add FK constraint on existing Juris_ID

Usage
-----
  python schema_ddl.py
"""

import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;DATABASE=Registrations_DW;Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Each entry is (description, sql).
# Executed in order; each is committed individually.
# ---------------------------------------------------------------------------

STEPS = [

    # -----------------------------------------------------------------------
    # DIM_Entity_Type
    # -----------------------------------------------------------------------
    ("DIM_Entity_Type", """
IF OBJECT_ID(N'dbo.DIM_Entity_Type', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Entity_Type (
        ID                   INT          NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Entity_Type_Code     VARCHAR(50)  NOT NULL,
        Entity_Type_Name     VARCHAR(200) NOT NULL,
        Entity_Type_Category VARCHAR(50)  NULL,
        CONSTRAINT UQ_EntityType_Code UNIQUE (Entity_Type_Code)
    );
END
"""),

    # -----------------------------------------------------------------------
    # DIM_Status
    # -----------------------------------------------------------------------
    ("DIM_Status", """
IF OBJECT_ID(N'dbo.DIM_Status', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Status (
        ID                 INT          NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Status_Code        VARCHAR(50)  NOT NULL,
        Status_Description VARCHAR(200) NOT NULL,
        Is_Active          BIT          NOT NULL DEFAULT 0,
        CONSTRAINT UQ_Status_Code UNIQUE (Status_Code)
    );
END
"""),

    # -----------------------------------------------------------------------
    # DIM_Company
    # -----------------------------------------------------------------------
    ("DIM_Company", """
IF OBJECT_ID(N'dbo.DIM_Company', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Company (
        ID                          BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Original_Formation_Juris_ID BIGINT        NOT NULL,
        Company_Name                NVARCHAR(500) NOT NULL,
        Company_Name_Normalized     VARCHAR(1000) NOT NULL,
        Record_Insert_Date          DATETIME      NOT NULL DEFAULT GETDATE(),
        Record_Update_Date          DATETIME      NOT NULL DEFAULT GETDATE(),

        CONSTRAINT FK_Company_Juris FOREIGN KEY (Original_Formation_Juris_ID)
            REFERENCES dbo.DIM_Jurisdiction (ID),
        CONSTRAINT UQ_Company_NormKey
            UNIQUE (Original_Formation_Juris_ID, Company_Name_Normalized)
    );
    CREATE INDEX IX_Company_Normalized ON dbo.DIM_Company (Company_Name_Normalized);
END
"""),

    # -----------------------------------------------------------------------
    # Fact_Registration -- add Juris_ID FK constraint if not yet present
    # -----------------------------------------------------------------------
    ("Fact_Registration: Juris_ID FK", """
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE  parent_object_id = OBJECT_ID(N'dbo.Fact_Registration')
      AND  name             = N'FK_FR_Juris'
)
    ALTER TABLE dbo.Fact_Registration
        ADD CONSTRAINT FK_FR_Juris
            FOREIGN KEY (Juris_ID) REFERENCES dbo.DIM_Jurisdiction (ID);
"""),

    # -----------------------------------------------------------------------
    # Fact_Registration -- Original_Formation_Juris_ID
    # -----------------------------------------------------------------------
    ("Fact_Registration: Original_Formation_Juris_ID", """
IF COL_LENGTH('dbo.Fact_Registration', 'Original_Formation_Juris_ID') IS NULL
    ALTER TABLE dbo.Fact_Registration
        ADD Original_Formation_Juris_ID BIGINT NULL
            CONSTRAINT FK_FR_OrigJuris
                FOREIGN KEY REFERENCES dbo.DIM_Jurisdiction (ID);
"""),

    # -----------------------------------------------------------------------
    # Fact_Registration -- Company_ID
    # -----------------------------------------------------------------------
    ("Fact_Registration: Company_ID", """
IF COL_LENGTH('dbo.Fact_Registration', 'Company_ID') IS NULL
    ALTER TABLE dbo.Fact_Registration
        ADD Company_ID BIGINT NULL
            CONSTRAINT FK_FR_Company
                FOREIGN KEY REFERENCES dbo.DIM_Company (ID);
"""),

    # -----------------------------------------------------------------------
    # DIM_Registration_Principal  (Type 2 SCD, individual-level)
    #
    # Natural key for SCD matching: (Registration_ID, First_Name, Middle_Name,
    # Last_Name, Name_Suffix) -- identifies the person.
    # Tracked columns (trigger a new row): Title, Name_Prefix.
    # -----------------------------------------------------------------------
    ("DIM_Registration_Principal", """
IF OBJECT_ID(N'dbo.DIM_Registration_Principal', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Registration_Principal (
        ID              BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Registration_ID BIGINT        NOT NULL,
        Title           VARCHAR(100)  NULL,
        Name_Prefix     VARCHAR(50)   NULL,
        First_Name      NVARCHAR(200) NULL,
        Middle_Name     NVARCHAR(200) NULL,
        Last_Name       NVARCHAR(200) NOT NULL,
        Name_Suffix     VARCHAR(50)   NULL,
        Valid_From      DATE          NOT NULL,
        Valid_To        DATE          NULL,
        Is_Current      BIT           NOT NULL DEFAULT 1,

        CONSTRAINT FK_DRP_Registration FOREIGN KEY (Registration_ID)
            REFERENCES dbo.Fact_Registration (ID)
    );
    CREATE INDEX IX_DRP_Registration ON dbo.DIM_Registration_Principal (Registration_ID);
    CREATE INDEX IX_DRP_IsCurrent    ON dbo.DIM_Registration_Principal (Is_Current);
END
"""),

    # -----------------------------------------------------------------------
    # DIM_Company_Principal  (structure only, populated later)
    # -----------------------------------------------------------------------
    ("DIM_Company_Principal", """
IF OBJECT_ID(N'dbo.DIM_Company_Principal', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DIM_Company_Principal (
        ID                     BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Company_ID             BIGINT        NOT NULL,
        Title                  VARCHAR(100)  NULL,
        Name_Prefix            VARCHAR(50)   NULL,
        First_Name             NVARCHAR(200) NULL,
        Middle_Name            NVARCHAR(200) NULL,
        Last_Name              NVARCHAR(200) NOT NULL,
        Name_Suffix            VARCHAR(50)   NULL,
        Source_Registration_ID BIGINT        NULL,
        Valid_From             DATE          NOT NULL,
        Valid_To               DATE          NULL,
        Is_Current             BIT           NOT NULL DEFAULT 1,

        CONSTRAINT FK_DCP_Company FOREIGN KEY (Company_ID)
            REFERENCES dbo.DIM_Company (ID),
        CONSTRAINT FK_DCP_SourceReg FOREIGN KEY (Source_Registration_ID)
            REFERENCES dbo.Fact_Registration (ID)
    );
    CREATE INDEX IX_DCP_Company   ON dbo.DIM_Company_Principal (Company_ID);
    CREATE INDEX IX_DCP_IsCurrent ON dbo.DIM_Company_Principal (Is_Current);
END
"""),

    # -----------------------------------------------------------------------
    # Staging_Registration
    #
    # Mirrors Fact_Registration data columns (all nullable -- data arrives
    # raw, FKs are resolved during incremental_load.py).
    # Adds staging-specific tracking columns.
    # No FK constraints -- staging is constraint-free by design.
    # -----------------------------------------------------------------------
    ("Staging_Registration", """
IF OBJECT_ID(N'dbo.Staging_Registration', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.Staging_Registration (
        -- Staging metadata
        Staging_ID            BIGINT       NOT NULL IDENTITY(1,1) PRIMARY KEY,
        Batch_ID              VARCHAR(100) NULL,
        Load_Date             DATETIME     NOT NULL DEFAULT GETDATE(),
        Processing_Status     VARCHAR(20)  NOT NULL DEFAULT 'Pending',
        Processing_Notes      VARCHAR(500) NULL,

        -- Business key
        Juris_ID              BIGINT       NULL,
        Juris_ID_Number       VARCHAR(100) NULL,

        -- Company identity
        Original_Formation_Juris_ID  BIGINT        NULL,
        Company_Name                 NVARCHAR(500) NULL,
        Company_Name_Normalized      VARCHAR(1000) NULL,
        Company_ID                   BIGINT        NULL,

        -- Dates
        Formation_Date_ID     INT  NULL,
        Annual_Report_Due_Date DATE NULL,
        Last_Filed_Date        DATE NULL,

        -- Status and entity type (raw strings from source)
        Status_State       VARCHAR(100) NULL,
        Status_ID          INT          NULL,
        Entity_Type_State  VARCHAR(100) NULL,
        Entity_Type_ID     INT          NULL,

        -- Structure
        LLC_Structure      VARCHAR(100) NULL,

        -- Principal address (raw)
        Principal_Address_1       NVARCHAR(500) NULL,
        Principal_Address_2       NVARCHAR(500) NULL,
        Principal_City            VARCHAR(200)  NULL,
        Principal_State           VARCHAR(50)   NULL,
        Principal_Postal_Code     VARCHAR(20)   NULL,
        Principal_Country         VARCHAR(100)  NULL,
        -- Principal address (computed during load)
        Principal_Address_1_NWS   VARCHAR(500)  NULL,
        Principal_Address_2_NWS   VARCHAR(500)  NULL,
        Address_CSZ_NWS           VARCHAR(200)  NULL,
        Principal_Address_ID      BIGINT        NULL,

        -- Registered agent (raw)
        Registered_Agent_Name                NVARCHAR(500) NULL,
        Registered_Agent_Street_Address_1    NVARCHAR(500) NULL,
        Registered_Agent_Street_Address_2    NVARCHAR(500) NULL,
        Registered_Agent_City                VARCHAR(200)  NULL,
        Registered_Agent_State               VARCHAR(50)   NULL,
        Registered_Agent_Postal_Code         VARCHAR(20)   NULL,
        Registered_Agent_Country             VARCHAR(100)  NULL,
        -- Registered agent (computed during load)
        RA_Address_1_NWS             VARCHAR(500) NULL,
        RA_Address_2_NWS             VARCHAR(500) NULL,
        RA_CSZ_NWS                   VARCHAR(200) NULL,
        Registered_Agent_Address_ID  BIGINT       NULL
    );
    CREATE INDEX IX_Staging_JurisKey ON dbo.Staging_Registration (Juris_ID, Juris_ID_Number);
    CREATE INDEX IX_Staging_Status   ON dbo.Staging_Registration (Processing_Status);
END
"""),

]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = False
    cur = conn.cursor()

    for description, sql in STEPS:
        print(f"  {description}...", end=' ')
        try:
            cur.execute(sql)
            conn.commit()
            print("OK")
        except Exception as e:
            conn.rollback()
            print(f"FAILED\n    {e}")
            raise

    conn.close()
    print("\nSchema setup complete.")


if __name__ == '__main__':
    main()
