#!/usr/bin/env python3
"""
Pull/export workbooks from Sigma to local JSON files ("workbooks as code").

IMPORTANT: This only manages workbooks that were *created through the spec*
(`POST /v2/workbooks/spec`) and are therefore tracked in config.yml under
`workbooks:`. It never scrapes arbitrary UI-built workbooks — those carry
UI-only features (trellis, tooltip, etc.) that don't round-trip through the
spec API and would silently lose fidelity. Set `manage_workbooks: true` to
opt in.

Default behaviour pulls the latest spec for every tracked workbook (refresh).
Use `--id` to *adopt* a specific workbook you know was created via the spec.

Usage:
    python pull_workbooks_from_sigma.py            # Refresh all tracked (spec-created) workbooks
    python pull_workbooks_from_sigma.py --id UUID  # Adopt/refresh a specific spec-created workbook

Environment variables:
    SIGMA_CLIENT_ID - API client ID (required)
    SIGMA_SECRET - API client secret (required)
    SIGMA_API_URL - API base URL (optional, reads from config.yml if not set)
    SIGMA_CLOUD - Cloud provider shorthand (optional, falls back to 'aws' if API URL not found)
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


def pull_workbook(client, workbook_id, output_dir, config, path=None):
    """Pull a single workbook and save to file."""
    print(f"\n📥 Pulling workbook: {workbook_id}")

    try:
        spec = client.get_workbook_spec(workbook_id)

        wb_name = spec.get('name', 'unnamed')
        # Reuse the existing filename if this workbook is already tracked, so a
        # rename in Sigma doesn't orphan the old file on disk.
        existing = config.get('workbooks', {}).get(workbook_id, {})
        file_name = existing.get('file') or (sanitize_filename(wb_name) + '.json')
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
    parser = argparse.ArgumentParser(description='Pull spec-created workbooks from Sigma')
    parser.add_argument('--id', help='Adopt/refresh a specific spec-created workbook by ID')
    parser.add_argument('--output', default='workbooks', help='Output directory')
    args = parser.parse_args()

    print("=" * 60)
    print("📥 Sigma Workbook Pull (spec-created only)")
    print("=" * 60)

    config = load_config()

    # Enabled by config, by a one-off workflow override, or implicitly when
    # adopting a specific workbook by --id.
    forced = os.environ.get('FORCE_WORKBOOKS', '').lower() == 'true'
    if not config.get('manage_workbooks') and not forced and not args.id:
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

    tracked = config.get('workbooks', {}) or {}

    # Build the list of (id, path) to pull.
    pull_targets = []
    if args.id:
        # Adopting a specific workbook — fetch its folder path for the config record.
        path = None
        try:
            path = client.get_workbook(args.id).get('path', '')
        except Exception as e:
            print(f"⚠️  Could not read metadata for {args.id}: {e}")
        pull_targets = [(args.id, path)]
    else:
        # Refresh every tracked (spec-created) workbook. No folder scraping —
        # we never discover UI-built workbooks that weren't created via the spec.
        if not tracked:
            print("ℹ️  No workbooks tracked yet. Create one via the spec "
                  "(commit a workbook JSON → sync_workbooks_to_sigma.py), "
                  "or adopt one with --id <workbookId>.")
            return
        pull_targets = [(wb_id, info.get('path')) for wb_id, info in tracked.items()]
        print(f"\n📋 Refreshing {len(pull_targets)} tracked (spec-created) workbook(s)")

    success = 0
    failed = 0

    for wb_id, path in pull_targets:
        if pull_workbook(client, wb_id, output_dir, config, path=path):
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
