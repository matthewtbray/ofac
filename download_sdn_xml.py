#!/usr/bin/env python3
"""
download_sdn_xml.py  --  Download SDN.XML from OFAC

Downloads the OFAC Specially Designated Nationals XML file, following the
302 redirect from the OFAC publication service to AWS S3 automatically.

Optionally uploads the file to Azure Blob Storage as 'SDN.XML' in the root
of the specified container.  export_results.py (--output-blob mode) will
then archive that blob into the run's log folder and remove the root copy.

Usage
-----
  python download_sdn_xml.py --output /tmp/sdn.xml
  python download_sdn_xml.py --output /tmp/sdn.xml --upload-blob

Environment variables
---------------------
  STORAGE_CONNECTION_STRING   Required when --upload-blob is set
"""
import argparse
import os
import shutil
import sys
import urllib.request

OFAC_SDN_URL = (
    'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML'
)


def _download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={'User-Agent': 'SDNMatch/2.0'})
    with urllib.request.urlopen(req) as resp, open(dest, 'wb') as f:
        shutil.copyfileobj(resp, f)


def _upload_to_blob(local_path: str, container: str) -> None:
    conn_str = os.environ.get('STORAGE_CONNECTION_STRING')
    if not conn_str:
        sys.exit("STORAGE_CONNECTION_STRING environment variable not set.")
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        sys.exit("azure-storage-blob not installed.  Run: pip install azure-storage-blob")
    svc  = BlobServiceClient.from_connection_string(conn_str)
    blob = svc.get_blob_client(container, 'SDN.XML')
    with open(local_path, 'rb') as f:
        blob.upload_blob(f, overwrite=True)
    print(f"  Uploaded → {container}/SDN.XML")


def main():
    ap = argparse.ArgumentParser(description='Download SDN.XML from OFAC')
    ap.add_argument('--output', required=True, metavar='PATH',
                    help='Local path to save SDN.XML (e.g. /tmp/sdn.xml)')
    ap.add_argument('--url', default=OFAC_SDN_URL, metavar='URL',
                    help='Override the OFAC download URL')
    ap.add_argument('--upload-blob', action='store_true',
                    help='Upload the file to Azure Blob Storage after download '
                         '(requires STORAGE_CONNECTION_STRING env var)')
    ap.add_argument('--container', default='sdn', metavar='CONTAINER',
                    help='Blob container for upload (default: sdn)')
    args = ap.parse_args()

    print('Downloading SDN.XML from OFAC...', flush=True)
    try:
        _download(args.url, args.output)
    except Exception as exc:
        print(f'ERROR: Download failed: {exc}', file=sys.stderr)
        sys.exit(1)

    size_mb = os.path.getsize(args.output) / 1_048_576
    print(f'  Saved: {args.output}  ({size_mb:.1f} MB)', flush=True)

    if args.upload_blob:
        print('Uploading SDN.XML to blob storage...', flush=True)
        try:
            _upload_to_blob(args.output, args.container)
        except Exception as exc:
            print(f'ERROR: Blob upload failed: {exc}', file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
