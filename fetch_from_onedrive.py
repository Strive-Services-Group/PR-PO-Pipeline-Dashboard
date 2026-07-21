#!/usr/bin/env python3
"""
Cloud fetch: pull Abdul's latest PR/PO exports from OneDrive via Microsoft Graph.

Runs inside the GitHub Action (see .github/workflows/fetch-from-onedrive.yml) so the
dashboard updates even when CK's laptop is off. The Power Automate flow saves the
email attachments into the OneDrive "Email-Drops" folder; this script reads that
folder with application (client-credentials) access, downloads the newest PR and PO
workbooks, writes them into the repo as pr.xlsx / po.xlsx, and regenerates
pr_steps.json. The workflow then commits anything that changed.

Environment (provided as GitHub Actions secrets):
    GRAPH_TENANT_ID       Entra tenant id
    GRAPH_CLIENT_ID       app registration (client) id
    GRAPH_CLIENT_SECRET   client secret value
    ONEDRIVE_USER         UPN/email whose OneDrive holds the folder (e.g. Chandan.kumar@striveservicesgroup.com)
    ONEDRIVE_FOLDER       optional; overrides the folder path search
Exit codes: 0 = success or nothing-new; non-zero = real error (fails the run).
"""
import io
import os
import sys
import subprocess

import requests
import openpyxl

REPO = os.path.dirname(os.path.abspath(__file__))
GRAPH = "https://graph.microsoft.com/v1.0"

def _req(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"ERROR: missing GitHub secret {name} - add it under repo "
                 f"Settings > Secrets and variables > Actions.")
    return v

TENANT = _req("GRAPH_TENANT_ID")
CLIENT_ID = _req("GRAPH_CLIENT_ID")
CLIENT_SECRET = _req("GRAPH_CLIENT_SECRET")
USER = _req("ONEDRIVE_USER")

# The local folder is  OneDrive - IFAHR\Documents\Claude\Projects\CRM Related\Email-Drops.
# Depending on whether Known Folder Move is on, "Documents" may or may not be part of
# the drive path, so try both. An explicit ONEDRIVE_FOLDER secret overrides the search.
FOLDER_CANDIDATES = [os.environ["ONEDRIVE_FOLDER"]] if os.environ.get("ONEDRIVE_FOLDER") else [
    "Documents/Claude/Projects/CRM Related/Email-Drops",
    "Claude/Projects/CRM Related/Email-Drops",
    "Documents/Claude/Projects/CRM Related/Email-Drops".replace(" ", "%20"),
]


def token():
    r = requests.post(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    if not r.ok:
        sys.exit(f"ERROR getting Graph token: {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


def list_folder(tok):
    """Return (folderPath, [children]) for the first candidate path that exists."""
    h = {"Authorization": "Bearer " + tok}
    last = None
    for path in FOLDER_CANDIDATES:
        url = f"{GRAPH}/users/{USER}/drive/root:/{path}:/children"
        r = requests.get(url, headers=h, timeout=30)
        if r.ok:
            return path, r.json().get("value", [])
        last = f"{r.status_code} {r.text[:200]} @ {path}"
    sys.exit(f"ERROR: none of the candidate folder paths worked. Last: {last}")


def download(tok, item):
    r = requests.get(f"{GRAPH}/users/{USER}/drive/items/{item['id']}/content",
                     headers={"Authorization": "Bearer " + tok}, timeout=60)
    if not r.ok:
        sys.exit(f"ERROR downloading {item['name']}: {r.status_code}")
    return r.content


def kind_of(data):
    try:
        ws = openpyxl.load_workbook(io.BytesIO(data), read_only=True).active
        hdr = set(next(ws.iter_rows(min_row=1, max_row=1, values_only=True)) or ())
    except Exception as e:
        print(f"  (couldn't read a workbook: {e})")
        return None
    if "Purchase order" in hdr:
        return "po"
    if "Purchase requisition" in hdr:
        return "pr"
    return None


def main():
    tok = token()
    folder, children = list_folder(tok)
    print(f"Reading OneDrive folder: {folder} ({len(children)} items)")

    # newest .xlsx of each kind, by lastModifiedDateTime
    best = {"pr": None, "po": None}
    xlsx = [c for c in children if c.get("name", "").lower().endswith(".xlsx") and "file" in c]
    xlsx.sort(key=lambda c: c.get("lastModifiedDateTime", ""), reverse=True)
    for c in xlsx:
        if best["pr"] and best["po"]:
            break
        data = download(tok, c)
        k = kind_of(data)
        if k and not best[k]:
            best[k] = (c, data)
            print(f"  {k.upper()}: {c['name']} (modified {c.get('lastModifiedDateTime')})")

    updated = []
    for kind, target in (("pr", "pr.xlsx"), ("po", "po.xlsx")):
        if not best[kind]:
            print(f"  {target}: no {kind.upper()} workbook found - skipped.")
            continue
        _c, data = best[kind]
        path = os.path.join(REPO, target)
        old = open(path, "rb").read() if os.path.exists(path) else None
        if old == data:
            print(f"  {target}: identical to repo copy - skipped.")
            continue
        with open(path, "wb") as f:
            f.write(data)
        updated.append(target)
        print(f"  {target}: updated.")

    if not updated:
        print("Nothing new to process.")
        return 0

    if "pr.xlsx" in updated:
        print("Regenerating pr_steps.json...")
        r = subprocess.run([sys.executable, os.path.join(REPO, "gen_pr_steps.py"),
                            os.path.join(REPO, "pr.xlsx")], cwd=REPO)
        if r.returncode != 0:
            sys.exit("ERROR: gen_pr_steps.py failed.")
    print("Done - files updated; the workflow will commit them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
