#!/usr/bin/env python3
"""
Pull/export workbooks from Sigma to local JSON files ("workbooks as code").

Discovery is **folder-scoped**: workbooks whose folder path is within one of the
configured `workbook_folders` prefixes are pulled. Folder selection is the trust
boundary — point it only at folders you know are built through the spec, since
Sigma exposes no "created via API vs UI" flag and UI-only features (trellis,
tooltip, …) don't round-trip through the spec API. Set `manage_workbooks: true`
to opt in. Already-tracked workbooks are always refreshed too.

Usage:
    python pull_workbooks_from_sigma.py            # Pull all workbooks in configured folders (+ refresh tracked)
    python pull_workbooks_from_sigma.py --id UUID  # Pull/adopt one specific workbook
    python pull_workbooks_from_sigma.py --folder "My Documents/Test"  # One-off folder, ignores config

Environment variables:
    SIGMA_CLIENT_ID - API client ID (required)
    SIGMA_SECRET - API client secret (required)
    SIGMA_API_URL - API base URL (optional, reads from config.yml if not set)
    SIGMA_CLOUD - Cloud provider shorthand (optional, falls back to 'aws' if API URL not found)
    FORCE_WORKBOOKS - 'true' to run even if manage_workbooks is false (workflow override)
"""

import os
import sys
import json
import yaml
import argparse
import requests
from pathlib import Path
from datetime import datetime

# API base URLs by cloud (fallback if not in config.yml)
CLOUD_URLS = {
    'aws': 'https://aws-api.sigmacomputing.com',
    'gcp': 'https://api.sigmacomputing.com',
    'azure': 'https://api.us.azure.sigmacomputing.com',
    'azure-us': 'https://api.us.azure.sigmacomputing.com',
    'azure-eu': 'https://api.eu.azure.sigmacomputing.com',
    'azure-ca': 'https://api.ca.azure.sigmacomputing.com',
    'azure-uk': 'https://api.uk.azure.sigmacomputing.com',
}


def load_config():
    config_path = Path('config.yml')
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config):
    with open('config.yml', 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


class SigmaClient:
    def __init__(self):
        self.client_id = os.environ.get('SIGMA_CLIENT_ID')
        self.client_secret = os.environ.get('SIGMA_SECRET')

        if not self.client_id or not self.client_secret:
            raise ValueError("SIGMA_CLIENT_ID and SIGMA_SECRET environment variables required")

        # Priority order for API URL:
        # 1. SIGMA_API_URL environment variable (explicit override)
        # 2. config.yml sigma_api_url (set by the HTML app)
        # 3. SIGMA_CLOUD environment variable mapped to URL
        # 4. Default to AWS

        self.base_url = os.environ.get('SIGMA_API_URL')

        if not self.base_url:
            config = load_config()
            self.base_url = config.get('sigma_api_url')

        if not self.base_url:
            self.cloud = os.environ.get('SIGMA_CLOUD', 'aws').lower()
            self.base_url = CLOUD_URLS.get(self.cloud)
            if not self.base_url:
                raise ValueError(
                    f"Invalid SIGMA_CLOUD: {self.cloud}. "
                    f"Valid options: {', '.join(CLOUD_URLS.keys())} "
                    f"Or set SIGMA_API_URL to the full URL."
                )

        self.base_url = self.base_url.rstrip('/')

        print(f"🔗 Using API: {self.base_url}")

        self.access_token = None
        self._authenticate()

    def _authenticate(self):
        print(f"🔐 Authenticating with Sigma...")

        response = requests.post(
            f"{self.base_url}/v2/auth/token",
            data={
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret
            }
        )

        if response.status_code != 200:
            raise Exception(f"Authentication failed: {response.text}")

        self.access_token = response.json()['access_token']
        print("✅ Authenticated successfully")

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

    def list_workbooks(self):
        """Get all workbooks from Sigma, following pagination."""
        workbooks = []
        next_page = None
        while True:
            url = f"{self.base_url}/v2/workbooks?limit=200"
            if next_page:
                url += f"&page={next_page}"
            response = requests.get(url, headers=self._headers())
            if response.status_code != 200:
                raise Exception(f"Failed to list workbooks: {response.text}")
            data = response.json()
            workbooks.extend(data.get('entries', []))
            next_page = data.get('nextPage')
            if not next_page:
                break
        return workbooks

    def get_workbook(self, workbook_id):
        """Get workbook metadata (name, path, etc.)."""
        response = requests.get(
            f"{self.base_url}/v2/workbooks/{workbook_id}",
            headers=self._headers()
        )
        if response.status_code != 200:
            raise Exception(f"Failed to get workbook: {response.text}")
        return response.json()

    def get_workbook_spec(self, workbook_id):
        """Get the JSON spec of a workbook."""
        response = requests.get(
            f"{self.base_url}/v2/workbooks/{workbook_id}/spec",
            headers={**self._headers(), 'Accept': 'application/json'}
        )
        if response.status_code != 200:
            raise Exception(f"Failed to get workbook spec: {response.text}")
        return response.json()


def sanitize_filename(name):
    """Convert name to a safe filename."""
    return name.lower().replace(' ', '-').replace('_', '-').\
        encode('ascii', 'ignore').decode().\
        replace('--', '-').strip('-')[:50] or 'unnamed'


def folder_matches(path, folders):
    """True if a workbook's folder `path` is within any configured folder prefix."""
    if not folders:
        return False
    path = (path or '').rstrip('/')
    for f in folders:
        f = (f or '').rstrip('/')
        if path == f or path.startswith(f + '/'):
            return True
    return False


def unique_file_name(base, workbook_id, config, used):
    """
    Return a filename that doesn't collide with another workbook. Folder pulls
    routinely hit duplicate workbook names, so disambiguate with a short id
    suffix rather than letting one file silently overwrite another.
    """
    mappings = config.get('workbooks', {})
    taken = set(used)
    for wb_id, info in mappings.items():
        if wb_id != workbook_id and isinstance(info, dict) and info.get('file'):
            taken.add(info['file'])
    if base not in taken:
        return base
    stem = base[:-5] if base.endswith('.json') else base
    return f"{stem}-{workbook_id[:8]}.json"


def pull_workbook(client, workbook_id, output_dir, config, path=None, used=None):
    """Pull a single workbook and save to file."""
    print(f"\n📥 Pulling workbook: {workbook_id}")
    if used is None:
        used = set()

    try:
        spec = client.get_workbook_spec(workbook_id)

        wb_name = spec.get('name', 'unnamed')
        # Reuse the existing filename if already tracked, so a rename in Sigma
        # doesn't orphan the old file on disk; otherwise pick a collision-free name.
        existing = config.get('workbooks', {}).get(workbook_id, {})
        file_name = existing.get('file') or unique_file_name(
            sanitize_filename(wb_name) + '.json', workbook_id, config, used)
        used.add(file_name)
        file_path = output_dir / file_name

        with open(file_path, 'w') as f:
            json.dump(spec, f, indent=2)

        print(f"   ✅ Saved: {file_path}")

        if 'workbooks' not in config:
            config['workbooks'] = {}

        config['workbooks'][workbook_id] = {
            'file': file_name,
            'name': wb_name,
            'path': path if path is not None else existing.get('path', ''),
            'last_pulled': datetime.utcnow().isoformat() + 'Z'
        }

        return True

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Pull workbooks from Sigma (folder-scoped)')
    parser.add_argument('--id', help='Pull/adopt a specific workbook by ID')
    parser.add_argument('--folder', action='append',
                        help='Folder path to pull (repeatable); overrides config workbook_folders')
    parser.add_argument('--output', default='workbooks', help='Output directory')
    args = parser.parse_args()

    print("=" * 60)
    print("📥 Sigma Workbook Pull (folder-scoped)")
    print("=" * 60)

    config = load_config()

    forced = os.environ.get('FORCE_WORKBOOKS', '').lower() == 'true'
    if not config.get('manage_workbooks') and not forced and not args.id and not args.folder:
        print("ℹ️  manage_workbooks is not enabled in config.yml — skipping workbook pull.")
        print("   Set 'manage_workbooks: true' (or dispatch with include_workbooks=true) to opt in.")
        return

    try:
        client = SigmaClient()
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    folders = args.folder if args.folder else (config.get('workbook_folders', []) or [])
    tracked = config.get('workbooks', {}) or {}

    # Build the set of (id, path) targets.
    targets = {}  # workbook_id -> path
    if args.id:
        path = None
        try:
            path = client.get_workbook(args.id).get('path', '')
        except Exception as e:
            print(f"⚠️  Could not read metadata for {args.id}: {e}")
        targets[args.id] = path
    else:
        if not folders and not tracked:
            print("⚠️  No 'workbook_folders' configured and nothing tracked yet.")
            print("   Add folder paths under 'workbook_folders:' in config.yml, or use --folder.")
            return

        # 1) Folder-scoped discovery (the trust boundary).
        if folders:
            print(f"\n📋 Discovering workbooks in folders: {folders}")
            all_wbs = client.list_workbooks()
            print(f"   {len(all_wbs)} workbooks in org; filtering by folder...")
            for w in all_wbs:
                if folder_matches(w.get('path', ''), folders):
                    targets[w['workbookId']] = w.get('path', '')
            print(f"   {len(targets)} match the configured folders")

        # 2) Always refresh anything already tracked, even if it moved out of a folder.
        for wb_id, info in tracked.items():
            targets.setdefault(wb_id, info.get('path'))

    success = 0
    failed = 0
    used_files = set()

    for wb_id, path in targets.items():
        if pull_workbook(client, wb_id, output_dir, config, path=path, used=used_files):
            success += 1
        else:
            failed += 1

    save_config(config)

    print("\n" + "=" * 60)
    print(f"✅ Pulled: {success}  ❌ Failed: {failed}")
    print(f"📂 Output: {output_dir}/")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
