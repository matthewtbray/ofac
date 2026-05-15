# -*- coding: cp1252 -*-

import xml.etree.ElementTree as ET
import pyodbc
import re

XML_FILE = r"C:\pythonscripts\sdn_advanced.xml"
CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=.;"
    "DATABASE=SDN;"
    "Trusted_Connection=yes;"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_namespace(element: ET.Element) -> str:
    match = re.match(r"\{(.+?)\}", element.tag)
    return f"{{{match.group(1)}}}" if match else ""


def find_children(root: ET.Element, ns: str, container: str, child: str) -> list:
    parent = root.find(f".//{ns}{container}")
    return parent.findall(f"{ns}{child}") if parent is not None else []


def text(element: ET.Element) -> str:
    return (element.text or "").strip()


# ---------------------------------------------------------------------------
# Parser functions � one per table
# ---------------------------------------------------------------------------

def parse_alias_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "AliasTypeValues", "AliasType")]


def parse_area_code_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "AreaCodeTypeValues", "AreaCodeType")]


def parse_calendar_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "CalendarTypeValues", "CalendarType")]


def parse_country(root, ns):
    return [
        (int(e.attrib["ID"]), e.attrib.get("ISO2") or None, text(e))
        for e in find_children(root, ns, "CountryValues", "Country")
    ]


def parse_country_relevance(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "CountryRelevanceValues", "CountryRelevance")]


def parse_detail_reference(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "DetailReferenceValues", "DetailReference")]


def parse_detail_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "DetailTypeValues", "DetailType")]


def parse_doc_name_status(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "DocNameStatusValues", "DocNameStatus")]


def parse_entry_event_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "EntryEventTypeValues", "EntryEventType")]


def parse_feature_type_group(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "FeatureTypeGroupValues", "FeatureTypeGroup")]


def parse_id_reg_doc_date_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "IDRegDocDateTypeValues", "IDRegDocDateType")]


def parse_id_reg_doc_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "IDRegDocTypeValues", "IDRegDocType")]


def parse_identity_feature_link_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "IdentityFeatureLinkTypeValues", "IdentityFeatureLinkType")]


def parse_legal_basis_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "LegalBasisTypeValues", "LegalBasisType")]


def parse_list(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "ListValues", "List")]


def parse_loc_part_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "LocPartTypeValues", "LocPartType")]


def parse_loc_part_value_status(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "LocPartValueStatusValues", "LocPartValueStatus")]


def parse_loc_part_value_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "LocPartValueTypeValues", "LocPartValueType")]


def parse_name_part_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "NamePartTypeValues", "NamePartType")]


def parse_party_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "PartyTypeValues", "PartyType")]


def parse_relation_quality(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "RelationQualityValues", "RelationQuality")]


def parse_relation_type(root, ns):
    return [
        (int(e.attrib["ID"]),
         1 if e.attrib.get("Symmetrical", "false").lower() == "true" else 0,
         text(e))
        for e in find_children(root, ns, "RelationTypeValues", "RelationType")
    ]


def parse_reliability(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "ReliabilityValues", "Reliability")]


def parse_sanctions_type(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "SanctionsTypeValues", "SanctionsType")]


def parse_script(root, ns):
    return [(int(e.attrib["ID"]), e.attrib.get("ScriptCode", ""), text(e))
            for e in find_children(root, ns, "ScriptValues", "Script")]


def parse_script_status(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "ScriptStatusValues", "ScriptStatus")]


def parse_validity(root, ns):
    return [(int(e.attrib["ID"]), text(e))
            for e in find_children(root, ns, "ValidityValues", "Validity")]


# --- FK-dependent tables ---

def parse_area_code(root, ns):
    return [
        (int(e.attrib["ID"]),
         int(e.attrib["CountryID"]),
         e.attrib["Description"],
         int(e.attrib["AreaCodeTypeID"]),
         e.text.strip() if e.text and e.text.strip() else None)
        for e in find_children(root, ns, "AreaCodeValues", "AreaCode")
    ]


def parse_organisation(root, ns):
    return [
        (int(e.attrib["ID"]), int(e.attrib["CountryID"]), text(e))
        for e in find_children(root, ns, "OrganisationValues", "Organisation")
    ]


def parse_party_sub_type(root, ns):
    return [
        (int(e.attrib["ID"]), int(e.attrib["PartyTypeID"]), text(e))
        for e in find_children(root, ns, "PartySubTypeValues", "PartySubType")
    ]


def parse_feature_type(root, ns):
    return [
        (int(e.attrib["ID"]), int(e.attrib["FeatureTypeGroupID"]), text(e))
        for e in find_children(root, ns, "FeatureTypeValues", "FeatureType")
    ]


def parse_decision_making_body(root, ns):
    return [
        (int(e.attrib["ID"]), int(e.attrib["OrganisationID"]), text(e))
        for e in find_children(root, ns, "DecisionMakingBodyValues", "DecisionMakingBody")
    ]


def parse_subsidiary_body(root, ns):
    return [
        (int(e.attrib["ID"]),
         1 if e.attrib.get("Notional", "false").lower() == "true" else 0,
         int(e.attrib["DecisionMakingBodyID"]),
         text(e))
        for e in find_children(root, ns, "SubsidiaryBodyValues", "SubsidiaryBody")
    ]


def parse_sanctions_program(root, ns):
    return [
        (int(e.attrib["ID"]), int(e.attrib["SubsidiaryBodyID"]), text(e))
        for e in find_children(root, ns, "SanctionsProgramValues", "SanctionsProgram")
    ]


def parse_legal_basis(root, ns):
    return [
        (int(e.attrib["ID"]),
         e.attrib.get("LegalBasisShortRef", ""),
         int(e.attrib["LegalBasisTypeID"]),
         int(e.attrib["SanctionsProgramID"]),
         text(e))
        for e in find_children(root, ns, "LegalBasisValues", "LegalBasis")
    ]


# ---------------------------------------------------------------------------
# Table definitions � (table_name, insert_sql, parser_fn)
# Order: parents before children for inserts; reversed for truncates
# ---------------------------------------------------------------------------

TABLE_IMPORTS = [
    # Independent tables
    ("AliasType",
     "INSERT INTO AliasType (ID, Name) VALUES (?,?)",
     parse_alias_type),
    ("AreaCodeType",
     "INSERT INTO AreaCodeType (ID, Name) VALUES (?,?)",
     parse_area_code_type),
    ("CalendarType",
     "INSERT INTO CalendarType (ID, Name) VALUES (?,?)",
     parse_calendar_type),
    ("Country",
     "INSERT INTO Country (ID, ISO2, Name) VALUES (?,?,?)",
     parse_country),
    ("CountryRelevance",
     "INSERT INTO CountryRelevance (ID, Name) VALUES (?,?)",
     parse_country_relevance),
    ("DetailReference",
     "INSERT INTO DetailReference (ID, Description) VALUES (?,?)",
     parse_detail_reference),
    ("DetailType",
     "INSERT INTO DetailType (ID, Name) VALUES (?,?)",
     parse_detail_type),
    ("DocNameStatus",
     "INSERT INTO DocNameStatus (ID, Name) VALUES (?,?)",
     parse_doc_name_status),
    ("EntryEventType",
     "INSERT INTO EntryEventType (ID, Name) VALUES (?,?)",
     parse_entry_event_type),
    ("FeatureTypeGroup",
     "INSERT INTO FeatureTypeGroup (ID, Name) VALUES (?,?)",
     parse_feature_type_group),
    ("IDRegDocDateType",
     "INSERT INTO IDRegDocDateType (ID, Name) VALUES (?,?)",
     parse_id_reg_doc_date_type),
    ("IDRegDocType",
     "INSERT INTO IDRegDocType (ID, Name) VALUES (?,?)",
     parse_id_reg_doc_type),
    ("IdentityFeatureLinkType",
     "INSERT INTO IdentityFeatureLinkType (ID, Name) VALUES (?,?)",
     parse_identity_feature_link_type),
    ("LegalBasisType",
     "INSERT INTO LegalBasisType (ID, Name) VALUES (?,?)",
     parse_legal_basis_type),
    ("List",
     "INSERT INTO List (ID, Name) VALUES (?,?)",
     parse_list),
    ("LocPartType",
     "INSERT INTO LocPartType (ID, Name) VALUES (?,?)",
     parse_loc_part_type),
    ("LocPartValueStatus",
     "INSERT INTO LocPartValueStatus (ID, Name) VALUES (?,?)",
     parse_loc_part_value_status),
    ("LocPartValueType",
     "INSERT INTO LocPartValueType (ID, Name) VALUES (?,?)",
     parse_loc_part_value_type),
    ("NamePartType",
     "INSERT INTO NamePartType (ID, Name) VALUES (?,?)",
     parse_name_part_type),
    ("PartyType",
     "INSERT INTO PartyType (ID, Name) VALUES (?,?)",
     parse_party_type),
    ("RelationQuality",
     "INSERT INTO RelationQuality (ID, Name) VALUES (?,?)",
     parse_relation_quality),
    ("RelationType",
     "INSERT INTO RelationType (ID, Symmetrical, Name) VALUES (?,?,?)",
     parse_relation_type),
    ("Reliability",
     "INSERT INTO Reliability (ID, Name) VALUES (?,?)",
     parse_reliability),
    ("SanctionsType",
     "INSERT INTO SanctionsType (ID, Name) VALUES (?,?)",
     parse_sanctions_type),
    ("Script",
     "INSERT INTO Script (ID, ScriptCode, Name) VALUES (?,?,?)",
     parse_script),
    ("ScriptStatus",
     "INSERT INTO ScriptStatus (ID, Name) VALUES (?,?)",
     parse_script_status),
    ("Validity",
     "INSERT INTO Validity (ID, Name) VALUES (?,?)",
     parse_validity),
    # FK-dependent tables (parents must already exist)
    ("AreaCode",
     "INSERT INTO AreaCode (ID, CountryID, Description, AreaCodeTypeID, Code) VALUES (?,?,?,?,?)",
     parse_area_code),
    ("Organisation",
     "INSERT INTO Organisation (ID, CountryID, Name) VALUES (?,?,?)",
     parse_organisation),
    ("PartySubType",
     "INSERT INTO PartySubType (ID, PartyTypeID, Name) VALUES (?,?,?)",
     parse_party_sub_type),
    ("FeatureType",
     "INSERT INTO FeatureType (ID, FeatureTypeGroupID, Name) VALUES (?,?,?)",
     parse_feature_type),
    ("DecisionMakingBody",
     "INSERT INTO DecisionMakingBody (ID, OrganisationID, Name) VALUES (?,?,?)",
     parse_decision_making_body),
    ("SubsidiaryBody",
     "INSERT INTO SubsidiaryBody (ID, Notional, DecisionMakingBodyID, Name) VALUES (?,?,?,?)",
     parse_subsidiary_body),
    ("SanctionsProgram",
     "INSERT INTO SanctionsProgram (ID, SubsidiaryBodyID, Name) VALUES (?,?,?)",
     parse_sanctions_program),
    ("LegalBasis",
     "INSERT INTO LegalBasis (ID, LegalBasisShortRef, LegalBasisTypeID, SanctionsProgramID, Name) VALUES (?,?,?,?,?)",
     parse_legal_basis),
]


# ---------------------------------------------------------------------------
# Shared date-period helpers (used by multiple sections)
# ---------------------------------------------------------------------------

def _b(s):
    return 1 if (s or "").lower() == "true" else 0


def _ich(parent, ns, tag):
    el = parent.find(f"{ns}{tag}") if parent is not None else None
    return int(el.text) if el is not None and el.text else None


def _parse_date_period(dp, ns):
    """Flatten a DatePeriod element to a 24-tuple of scalar fields, or None."""
    if dp is None:
        return None
    st  = dp.find(f"{ns}Start")
    en  = dp.find(f"{ns}End")
    sf  = st.find(f"{ns}From") if st is not None else None
    sto = st.find(f"{ns}To")   if st is not None else None
    ef  = en.find(f"{ns}From") if en is not None else None
    eto = en.find(f"{ns}To")   if en is not None else None
    ga  = lambda el, k: (el.attrib.get(k, "false") if el is not None else "false")
    return (
        int(dp.attrib.get("CalendarTypeID", 0)),
        _b(dp.attrib.get("YearFixed")), _b(dp.attrib.get("MonthFixed")), _b(dp.attrib.get("DayFixed")),
        _b(ga(st, "Approximate")), _b(ga(st, "YearFixed")), _b(ga(st, "MonthFixed")), _b(ga(st, "DayFixed")),
        _ich(sf,  ns, "Year"), _ich(sf,  ns, "Month"), _ich(sf,  ns, "Day"),
        _ich(sto, ns, "Year"), _ich(sto, ns, "Month"), _ich(sto, ns, "Day"),
        _b(ga(en, "Approximate")), _b(ga(en, "YearFixed")), _b(ga(en, "MonthFixed")), _b(ga(en, "DayFixed")),
        _ich(ef,  ns, "Year"), _ich(ef,  ns, "Month"), _ich(ef,  ns, "Day"),
        _ich(eto, ns, "Year"), _ich(eto, ns, "Month"), _ich(eto, ns, "Day"),
    )


# ---------------------------------------------------------------------------
# DateOfIssue
# ---------------------------------------------------------------------------

def import_date_of_issue(cursor, root, ns):
    doi = root.find(f"{ns}DateOfIssue")
    if doi is None:
        print("\nDateOfIssue element not found — skipping")
        return

    calendar_type_id = int(doi.attrib["CalendarTypeID"])
    year  = _ich(doi, ns, "Year")
    month = _ich(doi, ns, "Month")
    day   = _ich(doi, ns, "Day")

    cursor.execute("TRUNCATE TABLE DateOfIssue")
    cursor.execute(
        "INSERT INTO DateOfIssue (CalendarTypeID, Year, Month, Day) VALUES (?,?,?,?)",
        (calendar_type_id, year, month, day)
    )
    print("\nDateOfIssue: 1 row inserted")


# ---------------------------------------------------------------------------
# Location parser - returns rows for six tables in dependency order
# ---------------------------------------------------------------------------

def parse_locations(root, ns):
    locations      = []
    area_codes     = []
    countries      = []
    parts          = []
    part_values    = []
    fvr_rows       = []

    part_id   = 1
    value_id  = 1

    container = root.find(f".//{ns}Locations")
    if container is None:
        return locations, area_codes, countries, parts, part_values, fvr_rows

    for loc in container.findall(f"{ns}Location"):
        loc_id = int(loc.attrib["ID"])
        locations.append((loc_id,))

        ac = loc.find(f"{ns}LocationAreaCode")
        if ac is not None:
            area_codes.append((loc_id, int(ac.attrib["AreaCodeID"])))

        lc = loc.find(f"{ns}LocationCountry")
        if lc is not None:
            countries.append((
                loc_id,
                int(lc.attrib["CountryID"]),
                int(lc.attrib["CountryRelevanceID"]),
            ))

        for lp in loc.findall(f"{ns}LocationPart"):
            current_part_id = part_id
            part_id += 1
            parts.append((current_part_id, loc_id, int(lp.attrib["LocPartTypeID"])))

            for lpv in lp.findall(f"{ns}LocationPartValue"):
                is_primary = 1 if lpv.attrib.get("Primary", "false").lower() == "true" else 0
                val_el     = lpv.find(f"{ns}Value")
                cmt_el     = lpv.find(f"{ns}Comment")
                val        = (val_el.text or "").strip() if val_el is not None else ""
                comment    = (cmt_el.text or "").strip() or None if cmt_el is not None else None
                part_values.append((
                    value_id,
                    current_part_id,
                    is_primary,
                    int(lpv.attrib["LocPartValueTypeID"]),
                    int(lpv.attrib["LocPartValueStatusID"]),
                    val,
                    comment,
                ))
                value_id += 1

        for fvr in loc.findall(f"{ns}FeatureVersionReference"):
            fvr_rows.append((loc_id, int(fvr.attrib["FeatureVersionID"])))

    return locations, area_codes, countries, parts, part_values, fvr_rows


def import_locations(cursor, root, ns):
    locations, area_codes, countries, parts, part_values, fvr_rows = parse_locations(root, ns)

    print("\nClearing location tables...")
    for tbl in ("LocationFeatureVersionRef", "LocationPartValue",
                "LocationPart", "LocationCountry", "LocationAreaCode", "Location"):
        cursor.execute(f"DELETE FROM {tbl}")
        print(f"  Cleared {tbl}")

    print("\nInserting location data...")
    if locations:
        cursor.executemany("INSERT INTO Location (ID) VALUES (?)", locations)
    print(f"  {'Location':<30} {len(locations):>5} rows inserted")

    if area_codes:
        cursor.executemany(
            "INSERT INTO LocationAreaCode (LocationID, AreaCodeID) VALUES (?,?)",
            area_codes)
    print(f"  {'LocationAreaCode':<30} {len(area_codes):>5} rows inserted")

    if countries:
        cursor.executemany(
            "INSERT INTO LocationCountry (LocationID, CountryID, CountryRelevanceID) VALUES (?,?,?)",
            countries)
    print(f"  {'LocationCountry':<30} {len(countries):>5} rows inserted")

    if parts:
        cursor.executemany(
            "INSERT INTO LocationPart (ID, LocationID, LocPartTypeID) VALUES (?,?,?)",
            parts)
    print(f"  {'LocationPart':<30} {len(parts):>5} rows inserted")

    if part_values:
        cursor.executemany(
            "INSERT INTO LocationPartValue "
            "(ID, LocationPartID, IsPrimary, LocPartValueTypeID, LocPartValueStatusID, Value, Comment) "
            "VALUES (?,?,?,?,?,?,?)",
            part_values)
    print(f"  {'LocationPartValue':<30} {len(part_values):>5} rows inserted")

    # LocationFeatureVersionRef is inserted later (after DistinctParties) because
    # FeatureVersion rows must exist first to satisfy the FK constraint.
    print(f"  {'LocationFeatureVersionRef':<30} {len(fvr_rows):>5} rows deferred")
    return fvr_rows


# ---------------------------------------------------------------------------
# DistinctParties
# ---------------------------------------------------------------------------

def import_distinct_parties(cursor, root, ns):
    distinct_parties = []
    profiles         = []
    identities       = []
    name_part_groups = []
    aliases          = []
    doc_names        = []
    doc_name_parts   = []
    features         = []
    fversions        = []
    fv_details       = []
    fv_date_periods  = []

    alias_id_seq  = 1
    dnpart_id_seq = 1
    fvdet_id_seq  = 1

    container = root.find(f".//{ns}DistinctParties")
    if container is None:
        return

    for dp in container.findall(f"{ns}DistinctParty"):
        fixed_ref = int(dp.attrib["FixedRef"])
        distinct_parties.append((fixed_ref,))

        for profile in dp.findall(f"{ns}Profile"):
            profile_id = int(profile.attrib["ID"])
            profiles.append((profile_id, fixed_ref, int(profile.attrib["PartySubTypeID"])))

            for identity in profile.findall(f"{ns}Identity"):
                identity_id = int(identity.attrib["ID"])
                identities.append((
                    identity_id,
                    profile_id,
                    int(identity.attrib.get("FixedRef", 0)),
                    _b(identity.attrib.get("Primary")),
                    _b(identity.attrib.get("False")),
                ))

                npgs = identity.find(f"{ns}NamePartGroups")
                if npgs is not None:
                    for mpg in npgs.findall(f"{ns}MasterNamePartGroup"):
                        for npg in mpg.findall(f"{ns}NamePartGroup"):
                            name_part_groups.append((
                                int(npg.attrib["ID"]),
                                identity_id,
                                int(npg.attrib["NamePartTypeID"]),
                            ))

                for alias in identity.findall(f"{ns}Alias"):
                    alias_id = alias_id_seq
                    alias_id_seq += 1
                    aliases.append((
                        alias_id,
                        identity_id,
                        int(alias.attrib.get("FixedRef", 0)),
                        int(alias.attrib["AliasTypeID"]),
                        _b(alias.attrib.get("Primary")),
                        _b(alias.attrib.get("LowQuality")),
                    ))
                    for dn in alias.findall(f"{ns}DocumentedName"):
                        dn_id = int(dn.attrib["ID"])
                        doc_names.append((
                            dn_id,
                            alias_id,
                            int(dn.attrib.get("FixedRef", 0)),
                            int(dn.attrib["DocNameStatusID"]),
                        ))
                        for dnp in dn.findall(f"{ns}DocumentedNamePart"):
                            for npv in dnp.findall(f"{ns}NamePartValue"):
                                doc_name_parts.append((
                                    dnpart_id_seq,
                                    dn_id,
                                    int(npv.attrib["NamePartGroupID"]),
                                    int(npv.attrib["ScriptID"]),
                                    int(npv.attrib["ScriptStatusID"]),
                                    _b(npv.attrib.get("Acronym")),
                                    (npv.text or "").strip(),
                                ))
                                dnpart_id_seq += 1

            for feat in profile.findall(f"{ns}Feature"):
                feat_id = int(feat.attrib["ID"])
                iref    = feat.find(f"{ns}IdentityReference")
                features.append((
                    feat_id,
                    profile_id,
                    int(feat.attrib["FeatureTypeID"]),
                    int(iref.attrib["IdentityID"])               if iref is not None else None,
                    int(iref.attrib["IdentityFeatureLinkTypeID"]) if iref is not None else None,
                ))
                for fv in feat.findall(f"{ns}FeatureVersion"):
                    fv_id = int(fv.attrib["ID"])
                    cmt   = fv.find(f"{ns}Comment")
                    fversions.append((
                        fv_id,
                        feat_id,
                        int(fv.attrib["ReliabilityID"]),
                        (cmt.text or "").strip() or None if cmt is not None else None,
                    ))
                    for vd in fv.findall(f"{ns}VersionDetail"):
                        ref_id = vd.attrib.get("DetailReferenceID")
                        fv_details.append((
                            fvdet_id_seq,
                            fv_id,
                            int(vd.attrib["DetailTypeID"]),
                            int(ref_id) if ref_id else None,
                            (vd.text or "").strip() or None,
                        ))
                        fvdet_id_seq += 1
                    dp_tup = _parse_date_period(fv.find(f"{ns}DatePeriod"), ns)
                    if dp_tup is not None:
                        fv_date_periods.append((fv_id,) + dp_tup)

    DATE_PERIOD_COLS = (
        "CalendarTypeID, YearFixed, MonthFixed, DayFixed,"
        " StartApproximate, StartYearFixed, StartMonthFixed, StartDayFixed,"
        " StartFromYear, StartFromMonth, StartFromDay, StartToYear, StartToMonth, StartToDay,"
        " EndApproximate, EndYearFixed, EndMonthFixed, EndDayFixed,"
        " EndFromYear, EndFromMonth, EndFromDay, EndToYear, EndToMonth, EndToDay"
    )
    DATE_PERIOD_QS = ",".join(["?"] * 24)

    print("\nClearing DistinctParty tables...")
    for tbl in (
        "IDRegDocNameRef", "IDRegDocumentDate", "IDRegDocument",
        "SanctionsMeasure", "EntryEvent", "SanctionsEntry",
        "ProfileRelationship",
        "FeatureVersionDatePeriod", "FeatureVersionDetail", "FeatureVersion",
        "Feature", "DocumentedNamePart", "DocumentedName", "Alias",
        "NamePartGroup", "[Identity]", "Profile", "DistinctParty",
    ):
        cursor.execute(f"DELETE FROM {tbl}")
        print(f"  Cleared {tbl}")

    print("\nInserting DistinctParty data...")
    rows_map = [
        ("DistinctParty",
         "INSERT INTO DistinctParty (FixedRef) VALUES (?)",
         distinct_parties),
        ("Profile",
         "INSERT INTO Profile (ID, FixedRef, PartySubTypeID) VALUES (?,?,?)",
         profiles),
        ("[Identity]",
         "INSERT INTO [Identity] (ID, ProfileID, FixedRef, IsPrimary, IsFalse) VALUES (?,?,?,?,?)",
         identities),
        ("NamePartGroup",
         "INSERT INTO NamePartGroup (ID, IdentityID, NamePartTypeID) VALUES (?,?,?)",
         name_part_groups),
        ("Alias",
         "INSERT INTO Alias (ID, IdentityID, FixedRef, AliasTypeID, IsPrimary, LowQuality) VALUES (?,?,?,?,?,?)",
         aliases),
        ("DocumentedName",
         "INSERT INTO DocumentedName (ID, AliasID, FixedRef, DocNameStatusID) VALUES (?,?,?,?)",
         doc_names),
        ("DocumentedNamePart",
         "INSERT INTO DocumentedNamePart (ID, DocumentedNameID, NamePartGroupID, ScriptID, ScriptStatusID, Acronym, Value) VALUES (?,?,?,?,?,?,?)",
         doc_name_parts),
        ("Feature",
         "INSERT INTO Feature (ID, ProfileID, FeatureTypeID, IdentityID, IdentityFeatureLinkTypeID) VALUES (?,?,?,?,?)",
         features),
        ("FeatureVersion",
         "INSERT INTO FeatureVersion (ID, FeatureID, ReliabilityID, Comment) VALUES (?,?,?,?)",
         fversions),
        ("FeatureVersionDetail",
         "INSERT INTO FeatureVersionDetail (ID, FeatureVersionID, DetailTypeID, DetailReferenceID, Value) VALUES (?,?,?,?,?)",
         fv_details),
        ("FeatureVersionDatePeriod",
         f"INSERT INTO FeatureVersionDatePeriod (FeatureVersionID, {DATE_PERIOD_COLS}) VALUES (?,{DATE_PERIOD_QS})",
         fv_date_periods),
    ]
    for tbl, sql, rows in rows_map:
        if rows:
            cursor.executemany(sql, rows)
        print(f"  {tbl:<30} {len(rows):>6} rows inserted")


# ---------------------------------------------------------------------------
# IDRegDocuments
# ---------------------------------------------------------------------------

def import_id_reg_documents(cursor, root, ns):
    docs      = []
    doc_dates = []
    doc_nrefs = []

    DATE_PERIOD_COLS = (
        "CalendarTypeID, YearFixed, MonthFixed, DayFixed,"
        " StartApproximate, StartYearFixed, StartMonthFixed, StartDayFixed,"
        " StartFromYear, StartFromMonth, StartFromDay, StartToYear, StartToMonth, StartToDay,"
        " EndApproximate, EndYearFixed, EndMonthFixed, EndDayFixed,"
        " EndFromYear, EndFromMonth, EndFromDay, EndToYear, EndToMonth, EndToDay"
    )
    DATE_PERIOD_QS = ",".join(["?"] * 24)

    container = root.find(f".//{ns}IDRegDocuments")
    if container is None:
        return

    for doc in container.findall(f"{ns}IDRegDocument"):
        doc_id     = int(doc.attrib["ID"])
        issued_cty = doc.attrib.get("IssuedBy-CountryID")
        issued_loc = doc.attrib.get("IssuedIn-LocationID")
        reg_no_el  = doc.find(f"{ns}IDRegistrationNo")
        auth_el    = doc.find(f"{ns}IssuingAuthority")
        cmt_el     = doc.find(f"{ns}Comment")
        docs.append((
            doc_id,
            int(doc.attrib["IDRegDocTypeID"]),
            int(doc.attrib["IdentityID"]),
            int(issued_cty) if issued_cty else None,
            int(issued_loc) if issued_loc else None,
            int(doc.attrib["ValidityID"]),
            (reg_no_el.text or "").strip() if reg_no_el is not None else "",
            (auth_el.text  or "").strip() or None if auth_el  is not None else None,
            (cmt_el.text   or "").strip() or None if cmt_el   is not None else None,
        ))

        dd = doc.find(f"{ns}DocumentDate")
        if dd is not None:
            dp_tup = _parse_date_period(dd.find(f"{ns}DatePeriod"), ns)
            if dp_tup is not None:
                doc_dates.append((doc_id, int(dd.attrib["IDRegDocDateTypeID"])) + dp_tup)

        for dnr in doc.findall(f"{ns}DocumentedNameReference"):
            doc_nrefs.append((doc_id, int(dnr.attrib["DocumentedNameID"])))

    print("\nClearing IDRegDocument tables...")
    for tbl in ("IDRegDocNameRef", "IDRegDocumentDate", "IDRegDocument"):
        cursor.execute(f"DELETE FROM {tbl}")
        print(f"  Cleared {tbl}")

    print("\nInserting IDRegDocument data...")
    if docs:
        cursor.executemany(
            "INSERT INTO IDRegDocument "
            "(ID, IDRegDocTypeID, IdentityID, IssuedByCountryID, IssuedInLocationID,"
            " ValidityID, IDRegistrationNo, IssuingAuthority, Comment)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            docs)
    print(f"  {'IDRegDocument':<30} {len(docs):>6} rows inserted")

    if doc_dates:
        cursor.executemany(
            f"INSERT INTO IDRegDocumentDate"
            f" (IDRegDocumentID, IDRegDocDateTypeID, {DATE_PERIOD_COLS})"
            f" VALUES (?,?,{DATE_PERIOD_QS})",
            doc_dates)
    print(f"  {'IDRegDocumentDate':<30} {len(doc_dates):>6} rows inserted")

    if doc_nrefs:
        cursor.executemany(
            "INSERT INTO IDRegDocNameRef (IDRegDocumentID, DocumentedNameID) VALUES (?,?)",
            doc_nrefs)
    print(f"  {'IDRegDocNameRef':<30} {len(doc_nrefs):>6} rows inserted")


# ---------------------------------------------------------------------------
# ProfileRelationships
# ---------------------------------------------------------------------------

def import_profile_relationships(cursor, root, ns):
    # Build set of valid SanctionsEntry IDs from XML to avoid FK violations
    # from orphaned references in the source data.
    valid_se_ids = set()
    se_container = root.find(f".//{ns}SanctionsEntries")
    if se_container is not None:
        for se in se_container.findall(f"{ns}SanctionsEntry"):
            valid_se_ids.add(int(se.attrib["ID"]))

    rows = []
    container = root.find(f".//{ns}ProfileRelationships")
    if container is None:
        return
    for pr in container.findall(f"{ns}ProfileRelationship"):
        se_id = pr.attrib.get("SanctionsEntryID")
        se_id_val = int(se_id) if se_id else None
        if se_id_val not in valid_se_ids:
            se_id_val = None
        rows.append((
            int(pr.attrib["ID"]),
            int(pr.attrib["From-ProfileID"]),
            int(pr.attrib["To-ProfileID"]),
            int(pr.attrib["RelationTypeID"]),
            int(pr.attrib["RelationQualityID"]),
            _b(pr.attrib.get("Former")),
            se_id_val,
        ))

    print("\nClearing ProfileRelationship tables...")
    cursor.execute("DELETE FROM ProfileRelationship")
    print("  Cleared ProfileRelationship")

    print("\nInserting ProfileRelationship data...")
    if rows:
        cursor.executemany(
            "INSERT INTO ProfileRelationship"
            " (ID, FromProfileID, ToProfileID, RelationTypeID, RelationQualityID, Former, SanctionsEntryID)"
            " VALUES (?,?,?,?,?,?,?)",
            rows)
    print(f"  {'ProfileRelationship':<30} {len(rows):>6} rows inserted")


# ---------------------------------------------------------------------------
# SanctionsEntries
# ---------------------------------------------------------------------------

def import_sanctions_entries(cursor, root, ns):
    entries  = []
    events   = []
    measures = []

    container = root.find(f".//{ns}SanctionsEntries")
    if container is None:
        return

    for se in container.findall(f"{ns}SanctionsEntry"):
        se_id = int(se.attrib["ID"])
        entries.append((se_id, int(se.attrib["ProfileID"]), int(se.attrib["ListID"])))

        for ev in se.findall(f"{ns}EntryEvent"):
            date_el = ev.find(f"{ns}Date")
            cmt_el  = ev.find(f"{ns}Comment")
            events.append((
                int(ev.attrib["ID"]),
                se_id,
                int(ev.attrib["EntryEventTypeID"]),
                int(ev.attrib["LegalBasisID"]),
                int(date_el.attrib["CalendarTypeID"]) if date_el is not None else None,
                _ich(date_el, ns, "Year")  if date_el is not None else None,
                _ich(date_el, ns, "Month") if date_el is not None else None,
                _ich(date_el, ns, "Day")   if date_el is not None else None,
                (cmt_el.text or "").strip() or None if cmt_el is not None else None,
            ))

        for sm in se.findall(f"{ns}SanctionsMeasure"):
            dp_el  = sm.find(f"{ns}DatePeriod")
            cmt_el = sm.find(f"{ns}Comment")
            measures.append((
                int(sm.attrib["ID"]),
                se_id,
                int(sm.attrib["SanctionsTypeID"]),
                (cmt_el.text or "").strip() or None if cmt_el is not None else None,
                int(dp_el.attrib.get("CalendarTypeID", 0)) if dp_el is not None else None,
                _b(dp_el.attrib.get("YearFixed"))  if dp_el is not None else None,
                _b(dp_el.attrib.get("MonthFixed")) if dp_el is not None else None,
                _b(dp_el.attrib.get("DayFixed"))   if dp_el is not None else None,
            ))

    print("\nClearing SanctionsEntry tables...")
    for tbl in ("SanctionsMeasure", "EntryEvent", "SanctionsEntry"):
        cursor.execute(f"DELETE FROM {tbl}")
        print(f"  Cleared {tbl}")

    print("\nInserting SanctionsEntry data...")
    if entries:
        cursor.executemany(
            "INSERT INTO SanctionsEntry (ID, ProfileID, ListID) VALUES (?,?,?)",
            entries)
    print(f"  {'SanctionsEntry':<30} {len(entries):>6} rows inserted")

    if events:
        cursor.executemany(
            "INSERT INTO EntryEvent"
            " (ID, SanctionsEntryID, EntryEventTypeID, LegalBasisID,"
            "  CalendarTypeID, Year, Month, Day, Comment)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            events)
    print(f"  {'EntryEvent':<30} {len(events):>6} rows inserted")

    if measures:
        cursor.executemany(
            "INSERT INTO SanctionsMeasure"
            " (ID, SanctionsEntryID, SanctionsTypeID, Comment,"
            "  CalendarTypeID, YearFixed, MonthFixed, DayFixed)"
            " VALUES (?,?,?,?,?,?,?,?)",
            measures)
    print(f"  {'SanctionsMeasure':<30} {len(measures):>6} rows inserted")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Parsing XML...")
    with open(XML_FILE, encoding="utf-8-sig") as f:
        tree = ET.parse(f)
    root = tree.getroot()
    ns = get_namespace(root)

    parsed = []
    for table_name, insert_sql, parser_fn in TABLE_IMPORTS:
        rows = parser_fn(root, ns)
        parsed.append((table_name, insert_sql, rows))
        print(f"  {table_name:<30} {len(rows):>5} rows")

    with pyodbc.connect(CONNECTION_STRING) as conn:
        cursor = conn.cursor()
        cursor.fast_executemany = True

        # Delete children before parents to satisfy FK constraints
        print("\nClearing tables...")
        for table_name, _, _ in reversed(parsed):
            cursor.execute(f"DELETE FROM {table_name}")
            print(f"  Cleared {table_name}")

        # Insert parents before children
        print("\nInserting data...")
        for table_name, insert_sql, rows in parsed:
            if rows:
                cursor.executemany(insert_sql, rows)
            print(f"  {table_name:<30} {len(rows):>5} rows inserted")

        import_date_of_issue(cursor, root, ns)
        fvr_rows = import_locations(cursor, root, ns)
        import_distinct_parties(cursor, root, ns)

        # Now FeatureVersion rows exist — safe to insert LocationFeatureVersionRef
        print("\nInserting deferred LocationFeatureVersionRef rows...")
        if fvr_rows:
            cursor.executemany(
                "INSERT INTO LocationFeatureVersionRef (LocationID, FeatureVersionID) VALUES (?,?)",
                fvr_rows)
        print(f"  {'LocationFeatureVersionRef':<30} {len(fvr_rows):>6} rows inserted")

        import_id_reg_documents(cursor, root, ns)
        import_sanctions_entries(cursor, root, ns)
        import_profile_relationships(cursor, root, ns)

        conn.commit()
        print("\nAll done.")


if __name__ == "__main__":
    main()
