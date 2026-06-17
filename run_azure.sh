#!/bin/sh
# run_azure.sh  --  Full SDN matching pipeline for Azure Container Apps
#
# Runs four steps in sequence; exits non-zero on any failure.
#
# Required environment variables:
#   SQL_SERVER              Azure SQL logical server FQDN
#                           (e.g. myserver.database.windows.net)
#   SQL_USER                SQL login name
#   SQL_PASSWORD            SQL password  (inject via Container App secret)
#   STORAGE_CONNECTION_STRING  Azure Storage connection string
#
# Optional environment variables:
#   SDN_DB                  SDN database name          (default: SDN)
#   OUT_DB                  Output database name       (default: SDNReporting)
#   SDN_LIMIT               Max SDN entries: N or ALL  (default: ALL)
#   STORAGE_CONTAINER       Blob container name        (default: sdn)

set -e

export PYTHONPATH=/app

SDN_DB="${SDN_DB:-SDN}"
OUT_DB="${OUT_DB:-SDNReporting}"
SDN_LIMIT="${SDN_LIMIT:-ALL}"
STORAGE_CONTAINER="${STORAGE_CONTAINER:-sdn}"
SDN_XML="/tmp/sdn.xml"

echo ""
echo "===================================================================="
echo "  SDN Matching Pipeline  --  Azure Container App"
printf "  %s UTC\n" "$(date -u '+%Y-%m-%d %H:%M:%S')"
echo "===================================================================="

# ---- Validate required env vars ----------------------------------------
missing=""
for var in SQL_SERVER SQL_USER SQL_PASSWORD STORAGE_CONNECTION_STRING; do
    eval val=\$$var
    if [ -z "$val" ]; then
        missing="$missing $var"
    fi
done
if [ -n "$missing" ]; then
    echo "ERROR: Required environment variables not set:$missing"
    exit 1
fi

# ---- Step 1: Download SDN.XML from OFAC --------------------------------
echo ""
echo "[1/4] Downloading SDN.XML from OFAC..."
python /app/download_sdn_xml.py \
    --output      "$SDN_XML" \
    --upload-blob \
    --container   "$STORAGE_CONTAINER"

# ---- Step 2: Import SDN.XML into Azure SQL SDN database ----------------
echo ""
echo "[2/4] Importing SDN.XML into [$SQL_SERVER].[$SDN_DB]..."
python /app/xml_import.py \
    --xml      "$SDN_XML" \
    --server   "$SQL_SERVER" \
    --database "$SDN_DB" \
    --drop

# ---- Step 3: Run SDN matching ------------------------------------------
echo ""
echo "[3/4] Running SDN matching (limit: $SDN_LIMIT)..."

_run_match() {
    python /app/sdn_match_v2.py \
        --input-screening \
        --sdn-server   "$SQL_SERVER" --sdn-database "$SDN_DB" \
        --out-server   "$SQL_SERVER" --out-database "$OUT_DB" \
        --no-csv \
        "$@"
}

limit_args=""
[ "$SDN_LIMIT" != "ALL" ] && limit_args="--sdn-limit $SDN_LIMIT"
# shellcheck disable=SC2086
_run_match $limit_args

# ---- Step 4: Export results to blob and truncate -----------------------
echo ""
echo "[4/4] Exporting results to blob / truncating..."
python /app/export_results.py \
    --out-server        "$SQL_SERVER" --out-database "$OUT_DB" \
    --sdn-server        "$SQL_SERVER" --sdn-database "$SDN_DB" \
    --output-blob \
    --storage-container "$STORAGE_CONTAINER" \
    --truncate

echo ""
echo "===================================================================="
printf "  Pipeline complete  --  %s UTC\n" "$(date -u '+%Y-%m-%d %H:%M:%S')"
echo "===================================================================="
