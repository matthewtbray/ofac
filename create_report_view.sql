-- ============================================================
-- MatchingResults_Report  (VIEW)
-- Consolidates all positive match records from every
-- MatchingResults_* table into a single reporting surface.
-- Run this script once against SDNReporting.
-- Filter by Run_ID to scope to a specific matching run.
-- ============================================================

CREATE OR ALTER VIEW [dbo].[MatchingResults_Report] AS

-- ---- 1. Person primary-name matches ----------------------------------
-- Rows where the input person name matched an SDN individual's
-- primary (sdnEntry) name above the configured JW threshold.
SELECT
    Run_ID,
    Input_Record_ID,
    SDN_UID,
    SDN_Publish_Date,
    'Person_Name'                                          AS Match_Type,
    LTRIM(RTRIM(
        COALESCE(SourceFN + ' ', '')
      + COALESCE(SourceMN + ' ', '')
      + COALESCE(SourceLN,       '')))                    AS Input_Name,
    LTRIM(RTRIM(
        COALESCE(SDNFN + ' ', '')
      + COALESCE(SDNLN,      '')))                        AS SDN_Name,
    FirstName_JaroWinklerSimilarity                        AS FirstName_JW,
    LastName_JaroWinklerSimilarity                         AS LastName_JW,
    NULL                                                   AS FullName_JW,
    CAST(NULL AS INT)                                      AS AKA_UID,
    CAST(NULL AS VARCHAR(50))                              AS AKA_Category,
    CAST(NULL AS VARCHAR(500))                             AS LinkedTo_Text,
    CAST(NULL AS VARCHAR(100))                             AS Input_Phone,
    CAST(NULL AS VARCHAR(100))                             AS SDN_Phone,
    SDN_Type
FROM   dbo.MatchingResults_Person_Full
WHERE  Personal_Name_Match = 1

UNION ALL

-- ---- 2. Person AKA matches -------------------------------------------
-- Input person name matched an alias (akaList) of an SDN individual.
SELECT
    Run_ID,
    Input_Record_ID,
    SDN_UID,
    NULL,
    'Person_AKA',
    LTRIM(RTRIM(
        COALESCE(SourceFN + ' ', '')
      + COALESCE(SourceMN + ' ', '')
      + COALESCE(SourceLN,       ''))),
    LTRIM(RTRIM(
        COALESCE(SDNFN + ' ', '')
      + COALESCE(SDNLN,      ''))),
    FirstName_JaroWinklerSimilarity,
    LastName_JaroWinklerSimilarity,
    NULL,
    AKA_UID,
    AKA_Category,
    NULL,
    NULL,
    NULL,
    CAST(NULL AS VARCHAR(50))          -- AKA table has no SDN_Type column
FROM   dbo.MatchingResults_AKA
WHERE  Personal_Name_Match = 1

UNION ALL

-- ---- 3. Org / entity primary-name matches ----------------------------
-- Input entity name matched the primary name of an SDN entity.
-- All rows in this table share at least one org-name word with the
-- SDN entry; FullName_JW reflects the full-string similarity score.
SELECT
    Run_ID,
    Input_Record_ID,
    SDN_UID,
    SDN_Publish_Date,
    'Org_Name',
    SourceOrgName,
    SDNOrgName,
    NULL,
    NULL,
    FullName_JaroWinklerSimilarity,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    SDN_Type
FROM   dbo.MatchingResults_OrgName
WHERE  FullName_JaroWinklerSimilarity >= 85

UNION ALL

-- ---- 4. Org / entity AKA matches ------------------------------------
-- Input entity name matched an alias (akaList) of an SDN entity.
SELECT
    Run_ID,
    Input_Record_ID,
    SDN_UID,
    SDN_Publish_Date,
    'Org_AKA',
    SourceOrgName,
    SDNOrgName,
    NULL,
    NULL,
    FullName_JaroWinklerSimilarity,
    AKA_UID,
    AKA_Category,
    NULL,
    NULL,
    NULL,
    SDN_Type
FROM   dbo.MatchingResults_OrgName_AKA
WHERE  FullName_JaroWinklerSimilarity >= 85

UNION ALL

-- ---- 5. Linked-to matches -------------------------------------------
-- Input name matched a "Linked to: <name>" clause in SDN remarks.
-- SDN_Name is the linked-to entity name text from the remarks field.
SELECT
    Run_ID,
    Input_Record_ID,
    SDN_UID,
    SDN_Publish_Date,
    'LinkedTo',
    SourceName,
    LinkedTo_Text,
    NULL,
    NULL,
    FullName_JaroWinklerSimilarity,
    NULL,
    NULL,
    LinkedTo_Text,
    NULL,
    NULL,
    NULL
FROM   dbo.MatchingResults_LinkedTo

UNION ALL

-- ---- 6. Phone matches -----------------------------------------------
-- Input phone number shares at least the last 7 digits with an SDN
-- remarks phone number.  JW score is computed on digit strings only.
SELECT
    Run_ID,
    Input_Record_ID,
    SDN_UID,
    SDN_Publish_Date,
    'Phone',
    Input_Phone_Raw,
    SDN_Phone_Raw,
    NULL,
    NULL,
    JaroWinkler_Digits,
    NULL,
    NULL,
    NULL,
    Input_Phone_Raw,
    SDN_Phone_Raw,
    NULL
FROM   dbo.MatchingResults_Phone

UNION ALL

-- ---- 7. Address matches ---------------------------------------------
-- Input mailing address shares at least one normalized word with an
-- SDN address record.  JW scores reflect city and country similarity.
-- Input_Name / SDN_Name show the street + city + country for context.
SELECT
    Run_ID,
    Input_Record_ID,
    SDNEntry_UID                         AS SDN_UID,
    NULL,
    'Address',
    LTRIM(RTRIM(
        COALESCE(SourceAddress1 + ', ', '')
      + COALESCE(SourceCity    + ', ', '')
      + COALESCE(SourceCountry,         ''))),
    LTRIM(RTRIM(
        COALESCE(SDNAddress1 + ', ', '')
      + COALESCE(SDNCity    + ', ', '')
      + COALESCE(SDNCountry,         ''))),
    NULL,
    NULL,
    City_JaroWinklerSimilarity,          -- primary geo score for sorting/filtering
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL
FROM   dbo.MatchingResults_Address
WHERE  City_JaroWinklerSimilarity >= 70
AND   (Country_JaroWinklerSimilarity = 0 OR Country_JaroWinklerSimilarity >= 95);
