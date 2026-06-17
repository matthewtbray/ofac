FROM sdnmatchacr.azurecr.io/python:3.11-slim-bookworm

# Install Microsoft ODBC Driver 17 for SQL Server
# Uses gpg --dearmor (apt-key was removed in Debian 13+)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg2 ca-certificates \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 unixodbc-dev \
    && apt-get purge -y curl gnupg2 \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY download_sdn_xml.py \
     xml_import.py \
     sdn_match.py \
     sdn_match_v2.py \
     sdn_match_v2.cfg \
     export_results.py \
     create_report_view.sql \
     run_azure.sh \
     ./

RUN chmod +x run_azure.sh

ENTRYPOINT ["/bin/sh", "/app/run_azure.sh"]
