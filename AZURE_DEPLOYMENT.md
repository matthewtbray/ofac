# Deploying sdn_match_v2.py to Azure

This document covers six things:

1. Reading input files from Azure Blob Storage (as an alternative to `--input-csv`)
2. Connecting to the two SQL databases (SDN, SDNReporting) when they live in Azure SQL Database
3. Getting the source code into GitHub
4. Deploying the container to Azure Container Apps (initial deploy + redeploy from GitHub)
5. Running the container from Azure Data Factory
6. Whether the Python version is supported on Azure Container Apps

---

## 1. Reading input files from Azure Blob Storage

Today `--input-csv` expects a local file path. There are two practical ways to add Blob support:

### Option A (recommended): download-then-reuse

Add a new mutually-exclusive input option, `--input-blob`, that takes a Blob URL (or container+blob name). At startup, download the blob to a temp file and hand that path to the existing `load_input_csv()` — no changes needed to the CSV-parsing code itself.

```python
# requirements.txt addition:
#   azure-storage-blob>=12.19
#   azure-identity>=1.15

import tempfile
from azure.storage.blob import BlobClient
from azure.identity import DefaultAzureCredential

def download_input_blob(blob_url: str) -> str:
    """
    Downloads a CSV from Azure Blob Storage to a local temp file and
    returns the temp file path. Uses DefaultAzureCredential, which works
    with a Managed Identity in Azure or `az login` locally -- no
    connection string needed.
    """
    credential = DefaultAzureCredential()
    blob = BlobClient.from_blob_url(blob_url, credential=credential)

    fd, tmp_path = tempfile.mkstemp(suffix='.csv')
    with open(fd, 'wb') as f:
        f.write(blob.download_blob().readall())
    return tmp_path
```

Then in `main()`:

```python
inp.add_argument('--input-blob', metavar='URL',
                 help='Azure Blob URL of the input CSV (downloaded to a temp file)')
...
if args.input_blob:
    csv_path = download_input_blob(args.input_blob)
    input_records = load_input_csv(csv_path)
```

`DefaultAzureCredential` tries several auth methods in order: environment variables
(`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID`), Managed Identity (when
running in Azure), and `az login` (when running locally). This means the same code
works unchanged on your laptop and in a container with a Managed Identity assigned.

### Option B: stream into memory

If the CSV is small enough to fit in memory, skip the temp file and use
`io.StringIO` to wrap the downloaded bytes, then feed that directly to Python's
`csv.DictReader` (the same reader `load_input_csv` already uses). This avoids
touching the filesystem but requires a small refactor of `load_input_csv` to
accept either a path or a file-like object.

### Permissions needed

- The blob container needs **Storage Blob Data Reader** granted to whichever
  identity runs the container (a User-Assigned Managed Identity is the
  recommended choice for Container Apps — see Section 4).
- The Blob URL itself doesn't need a SAS token if Managed Identity is used;
  a plain `https://<account>.blob.core.windows.net/<container>/<blob>` URL works.

---

## 2. Connecting to the three Azure SQL databases

The script already supports SQL-authentication connection strings via the
`SQL_USER` / `SQL_PASSWORD` environment variables (see `_conn_str()` —
[sdn_match_v2.py:3778](sdn_match_v2.py)). To point at Azure SQL Database:

### 2.1 Database layout

Each of the two logical databases (`SDN`, `SDNReporting`) becomes its own
**Azure SQL Database**. They can both live on the **same logical server**
(e.g. `myserver.database.windows.net`) — Azure SQL doesn't support
cross-database queries, but as confirmed earlier, this script never issues any
(each `pyodbc.connect()` targets exactly one database).

Note: the address-abbreviation table (`dbo.Address_Abbreviation`) is now read
from the `SDN` database, so it must exist there.

### 2.2 Firewall / networking

- In the Azure SQL **server** resource, under **Networking**, enable
  **"Allow Azure services and resources to access this server"** so the
  Container App can reach it without a static IP allow-list.
- If you're running from your own machine for testing, add your client IP
  under the same Networking blade.

### 2.3 Authentication options

**Option A — SQL login (simplest)**

Create a SQL login/user with appropriate permissions (db_datareader on SDN;
db_datareader + db_datawriter + db_ddladmin on SDNReporting, since the script
creates/drops tables there).

```sql
-- run once on each database, as the server admin
CREATE USER sdn_match_app WITH PASSWORD = 'StrongPassword!123';
ALTER ROLE db_datareader ADD MEMBER sdn_match_app;          -- SDN, SDNReporting
ALTER ROLE db_datawriter ADD MEMBER sdn_match_app;          -- SDNReporting only
ALTER ROLE db_ddladmin    ADD MEMBER sdn_match_app;          -- SDNReporting only
```

Then set environment variables for the container:

```
SQL_USER=sdn_match_app
SQL_PASSWORD=StrongPassword!123
```

`_conn_str()` will automatically switch to `UID=...;PWD=...;Encrypt=yes;` mode
when these are set.

**Option B — Managed Identity (passwordless, more secure)**

Assign the Container App a User-Assigned Managed Identity, then create a
contained user *for that identity* in each database:

```sql
CREATE USER [sdn-match-identity] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [sdn-match-identity];
-- etc.
```

This requires switching `pyodbc` to use an access token instead of
`UID/PWD`. It's a larger code change (acquire a token via
`azure-identity`'s `DefaultAzureCredential().get_token(...)` and pass it
via `pyodbc`'s `attrs_before` / `SQL_COPT_SS_ACCESS_TOKEN`). Recommended as
a follow-up once the SQL-login path is working — flag if you'd like this
implemented.

### 2.4 Putting it together — example run

```bash
SQL_USER=sdn_match_app SQL_PASSWORD='StrongPassword!123' \
python sdn_match_v2.py \
  --input-screening \
  --sdn-server      myserver.database.windows.net --sdn-database SDN \
  --out-server      myserver.database.windows.net --out-database SDNReporting \
  --no-csv
```

---

## 3. Getting the source code into GitHub

```bash
cd C:\pythonscripts

# One-time setup
git init
git add sdn_match_v2.py sdn_match_v2.cfg requirements.txt Dockerfile
git commit -m "Initial commit of SDN matching v2"

# Create the GitHub repo and push (requires GitHub CLI: gh auth login first)
gh repo create <your-org-or-user>/sdn-match-v2 --private --source=. --remote=origin --push
```

A few things worth adding before the first commit:

- **`.gitignore`** — exclude local artifacts:
  ```
  __pycache__/
  *.pyc
  *.csv
  .venv/
  ```
- **Never commit `SQL_PASSWORD` or any credentials.** Keep them in
  GitHub Actions secrets / Azure Key Vault, not in `sdn_match_v2.cfg` or
  any committed file.

If the repo already exists and you're just adding these new files
(`requirements.txt`, `Dockerfile`):

```bash
git add requirements.txt Dockerfile
git commit -m "Add Docker packaging for Azure deployment"
git push
```

---

## 4. Deploying to Azure Container Apps

This script is a **run-to-completion batch job**, not a web server, so the
right Azure Container Apps primitive is a **Container Apps Job**
(`az containerapp job`), not a regular Container App (which expects an
HTTP listener and will be restarted/scaled based on traffic).

### 4.1 One-time prerequisites

```bash
# Resource group + Azure Container Registry
az group create --name rg-sdn-match --location eastus
az acr create --resource-group rg-sdn-match --name sdnmatchacr --sku Basic

# Container Apps environment (shared by jobs/apps)
az containerapp env create \
  --resource-group rg-sdn-match \
  --name sdn-match-env \
  --location eastus
```

### 4.2 Initial build & push

```bash
cd C:\pythonscripts

# Build the image in ACR (no local Docker needed)
az acr build --registry sdnmatchacr --image sdn-match:latest .
```

### 4.3 Create the Container Apps Job

```bash
az containerapp job create \
  --resource-group rg-sdn-match \
  --name sdn-match-job \
  --environment sdn-match-env \
  --trigger-type Manual \
  --replica-timeout 3600 \
  --replica-retry-limit 0 \
  --parallelism 1 \
  --image sdnmatchacr.azurecr.io/sdn-match:latest \
  --registry-server sdnmatchacr.azurecr.io \
  --cpu 2 --memory 4Gi \
  --env-vars SQL_USER=sdn_match_app SQL_PASSWORD=secretref:sql-password \
  --secrets sql-password=StrongPassword!123 \
  --command "python" \
  --args "sdn_match_v2.py" "--input-screening" "--no-csv" \
          "--sdn-server" "myserver.database.windows.net" \
          "--out-server" "myserver.database.windows.net"
```

Notes:
- `--trigger-type Manual` means the job runs only when explicitly started
  (via CLI, REST API, or — see Section 5 — Azure Data Factory).
  Use `Schedule` instead if you want it to run on a cron schedule
  automatically.
- `--replica-retry-limit 0` avoids silently re-running a partially-completed
  matching run; investigate failures manually instead.
- `secretref:` + `--secrets` keeps the password out of the job definition's
  plain-text env-var list (still visible to anyone with read access to the
  job, but separated from the `--env-vars` list for clarity — for real
  production use, pull from **Azure Key Vault** via a Key Vault reference
  instead).

### 4.4 Running it manually

```bash
az containerapp job start --resource-group rg-sdn-match --name sdn-match-job
```

### 4.5 Redeploying after code changes (manual)

```bash
az acr build --registry sdnmatchacr --image sdn-match:latest .
az containerapp job update \
  --resource-group rg-sdn-match --name sdn-match-job \
  --image sdnmatchacr.azurecr.io/sdn-match:latest
```

### 4.6 Continuous deployment from GitHub (CI/CD)

The simplest approach is a GitHub Actions workflow that builds the image on
every push to `main` and updates the job. Create
`.github/workflows/deploy.yml`:

```yaml
name: Build and deploy sdn-match-job

on:
  push:
    branches: [main]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Azure login
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Build and push image to ACR
        run: |
          az acr build --registry sdnmatchacr --image sdn-match:${{ github.sha }} .

      - name: Update Container Apps Job
        run: |
          az containerapp job update \
            --resource-group rg-sdn-match \
            --name sdn-match-job \
            --image sdnmatchacr.azurecr.io/sdn-match:${{ github.sha }}
```

Setup steps:

1. Create a service principal with rights to your resource group and store
   its JSON credentials in a GitHub repo secret named `AZURE_CREDENTIALS`:
   ```bash
   az ad sp create-for-rbac --name sdn-match-deploy \
     --role contributor \
     --scopes /subscriptions/<sub-id>/resourceGroups/rg-sdn-match \
     --sdk-auth
   ```
   (Paste the JSON output into the GitHub secret.)
2. Push to `main` — the workflow builds a new image tagged with the commit
   SHA and points the job at it. Each run after that will use the new image.

Alternatively, `az containerapp job github-action add` can scaffold a
similar workflow file for you automatically.

---

## 5. Running the Container App Job from Azure Data Factory

Azure Data Factory has no native "Container Apps Job" activity, but ADF's
**Web Activity** can call the Container Apps **Jobs REST API** directly to
start a run. This is the recommended approach.

### 5.1 Give ADF permission to start the job

1. Enable a **system-assigned managed identity** on the Data Factory.
2. Grant that identity the **Contributor** role (or the more narrow
   **Container Apps Jobs Operator** role, if available in your region) on
   the `sdn-match-job` resource (or the resource group).

### 5.2 ADF pipeline — Web Activity

Add a **Web Activity** with:

- **URL**:
  ```
  https://management.azure.com/subscriptions/<sub-id>/resourceGroups/rg-sdn-match/providers/Microsoft.App/jobs/sdn-match-job/start?api-version=2024-03-01
  ```
- **Method**: `POST`
- **Authentication**: `System Assigned Managed Identity`
- **Resource**: `https://management.azure.com/`
- **Body**: `{}` (empty JSON object — uses the job's existing configuration)

This call returns immediately with an execution name/ID; the job runs
asynchronously in Container Apps.

### 5.3 (Optional) Waiting for completion

If the pipeline needs to wait for the matching run to finish before
continuing (e.g. before a downstream step that reads `SDNReporting`), add a
loop:

1. **Web Activity** — POST `.../start` (as above), capture the
   `name` field from the response (the execution ID).
2. **Until Activity** containing a **Web Activity** that polls:
   ```
   GET https://management.azure.com/subscriptions/<sub-id>/resourceGroups/rg-sdn-match/providers/Microsoft.App/jobs/sdn-match-job/executions/<execution-name>?api-version=2024-03-01
   ```
   checking the response's `properties.status` for `Succeeded` / `Failed`.
3. Add a **Wait Activity** (e.g. 60s) inside the loop between polls.

### 5.4 Alternative: Azure Function or Logic App wrapper

If you'd rather not build the polling logic in ADF directly, wrap the
"start job and wait" logic in a small **Azure Function** (Python, using the
`azure-mgmt-appcontainers` SDK) and call that single Function from ADF via
Web Activity or Azure Function Activity. This is more code up front but
keeps the ADF pipeline simple and makes the polling logic unit-testable.

---

## 6. Is the Python version supported on Azure Container Apps?

**Yes — and it's not really an Azure-specific question.** Azure Container
Apps runs arbitrary Linux containers; it doesn't impose a Python version
requirement at all. Whatever Python version is baked into your Docker image
(via the `FROM python:3.11-slim` base image in the provided
[Dockerfile](Dockerfile)) is what runs.

Checking the script itself: it uses only widely-supported features —
f-strings, `@dataclass`, `typing.Optional`, `concurrent.futures`,
`argparse` — all available since **Python 3.7**. There's no use of
3.10+-only syntax (no `match` statements, no `X | Y` union type hints, no
walrus operator). So:

- `python:3.11-slim` (current Dockerfile) — fully supported, recommended.
- Anything from Python 3.8 through the latest 3.x — works fine.

The only version-sensitive pieces are the **third-party packages**
(`pyodbc`, `rapidfuzz`, `duckdb`) — all of these publish wheels for current
Python versions including 3.11/3.12, so `pip install -r requirements.txt`
inside the `python:3.11-slim` image will work without needing to compile
anything from source.
