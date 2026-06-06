#!/usr/bin/env python3
"""
Sync workbook JSON files to Sigma Computing via the API ("workbooks as code").

On update, a round-trip safety check runs first: the live workbook spec is
fetched and compared against the incoming spec, and the push is BLOCKED if it
would remove pages, elements, or top-level fields that exist live (a spec the
workbook's spec doesn't fully represent, or a stale local copy). This prevents
silently destroying content. Override intentionally with
ALLOW_WORKBOOK_REMOVALS=true.

Usage:
    python sync_workbooks_to_sigma.py workbooks/sales-dashboard.json ...
    python sync_workbooks_to_sigma.py --all

Environment variables:
    SIGMA_CLIENT_ID - API client ID (required)
    SIGMA_SECRET - API client secret (required)
    SIGMA_API_URL - API base URL (optional, reads from config.yml if not set)
    SIGMA_CLOUD - Cloud provider shorthand (optional, falls back to 'aws' if API URL not found)
    SIGMA_FOLDER_ID - Default folder for newly-created workbooks (optional)
    ALLOW_WORKBOOK_REMOVALS - 'true' to bypass the round-trip removal guard
"""

import os
import sys
import json
import yaml
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

# Response-only fields that must be stripped before creating a new workbook.
CREATE_STRIP_FIELDS = [
    'workbookId', 'ownerId', 'createdBy', 'updatedBy', 'createdAt', 'updatedAt',
    'documentVersion', 'latestDocumentVersion', 'url', 'path',
]

# Volatile / response-only fields ignored when comparing live vs incoming specs.
VOLATILE_FIELDS = {
    'documentVersion', 'latestDocumentVersion', 'url', 'createdAt', 'updatedAt',
    'createdBy', 'updatedBy', 'ownerId', 'path', 'workbookId',
}


def _elem_key(e):
    return e.get('elementId') or e.get('id') or e.get('name')


def roundtrip_removals(live, incoming):
    """
    Round-trip safety check. Returns a list of human-readable descriptions of
    content present in the LIVE workbook that the INCOMING spec would remove.

    Because Sigma exposes no "spec coverage" flag, this guards the real danger:
    pushing a spec that's missing pages/elements/fields the live workbook has
    (whether from a spec-coverage gap or a stale base) silently destroys them.
    Pure value edits are allowed; only removals are flagged.
    """
    issues = []

    for k in live:
        if k in VOLATILE_FIELDS:
            continue
        if k not in incoming:
            issues.append(f"top-level field '{k}'")

    live_pages = {_elem_key(p): p for p in live.get('pages', [])}
    inc_pages = {_elem_key(p): p for p in incoming.get('pages', [])}
    for pid, p in live_pages.items():
        if pid not in inc_pages:
            issues.append(f"page '{p.get('name', pid)}'")
            continue
        live_elems = {_elem_key(e): e for e in p.get('elements', [])}
        inc_elems = {_elem_key(e): e for e in inc_pages[pid].get('elements', [])}
        for eid, e in live_elems.items():
            if eid not in inc_elems:
                issues.append(f"element '{e.get('name', eid)}' on page '{p.get('name', pid)}'")

    return issues


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
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def get_workbook_spec(self, workbook_id):
        response = requests.get(
            f"{self.base_url}/v2/workbooks/{workbook_id}/spec",
            headers=self._headers()
        )
        if response.status_code != 200:
            raise Exception(f"Failed to get workbook spec: {response.text}")
        return response.json()

    def create_workbook(self, spec):
        """Create a new workbook from a JSON spec."""
        response = requests.post(
            f"{self.base_url}/v2/workbooks/spec",
            headers=self._headers(),
            json=spec
        )
        if response.status_code not in [200, 201]:
            raise Exception(f"Failed to create workbook: {response.text}")
        return response.json()

    def update_workbook(self, workbook_id, spec):
        """Update an existing workbook from a JSON spec."""
        response = requests.put(
            f"{self.base_url}/v2/workbooks/{workbook_id}/spec",
            headers=self._headers(),
            json=spec
        )
        if response.status_code != 200:
            raise Exception(f"Failed to update workbook: {response.text}")
        return response.json()


def get_workbook_id_for_file(file_path, config):
    """Look up the Sigma workbook ID for a file path from config.yml."""
    file_name = Path(file_path).name
    for wb_id, info in config.get('workbooks', {}).items():
        if isinstance(info, dict) and info.get('file') == file_name:
            return wb_id
    return None


def sync_file(client, file_path, config):
    """Sync a single workbook file to Sigma."""
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"⚠️  File not found: {file_path}")
        return False

    print(f"\n📄 Processing: {file_path.name}")

    with open(file_path) as f:
        spec = json.load(f)

    wb_name = spec.get('name', file_path.stem)

    workbook_id = get_workbook_id_for_file(file_path, config)
    if not workbook_id and spec.get('workbookId'):
        workbook_id = spec['workbookId']

    try:
        if workbook_id:
            # Round-trip safety check: never silently overwrite live content the
            # incoming spec doesn't represent.
            try:
                live = client.get_workbook_spec(workbook_id)
                removals = roundtrip_removals(live, spec)
            except Exception as e:
                print(f"   ⚠️  Could not run round-trip check ({e}); proceeding")
                removals = []

            allow_removals = os.environ.get('ALLOW_WORKBOOK_REMOVALS', '').lower() == 'true'
            if removals and not allow_removals:
                print(f"   🛑 Round-trip safety check FAILED for {wb_name}.")
                print(f"      Pushing this spec would REMOVE {len(removals)} item(s) present in the live workbook:")
                for r in removals[:20]:
                    print(f"        - {r}")
                if len(removals) > 20:
                    print(f"        … and {len(removals) - 20} more")
                print("      This usually means the spec doesn't fully represent the live workbook")
                print("      (spec-coverage gap) or your local copy is stale. Re-pull and reconcile,")
                print("      or set ALLOW_WORKBOOK_REMOVALS=true to override intentionally.")
                return False
            elif removals:
                print(f"   ⚠️  Overriding round-trip check — {len(removals)} removal(s) allowed via ALLOW_WORKBOOK_REMOVALS")

            print(f"   Updating workbook: {workbook_id}")
            client.update_workbook(workbook_id, spec)
            print(f"   ✅ Updated: {wb_name}")
        else:
            print(f"   Creating new workbook: {wb_name}")

            spec_clean = {k: v for k, v in spec.items() if k not in CREATE_STRIP_FIELDS}

            if 'schemaVersion' in spec_clean and isinstance(spec_clean['schemaVersion'], str):
                spec_clean['schemaVersion'] = int(spec_clean['schemaVersion'].replace('v', ''))
            elif 'schemaVersion' not in spec_clean:
                spec_clean['schemaVersion'] = 1

            if 'folderId' not in spec_clean:
                folder_id = config.get('default_folder_id') or os.environ.get('SIGMA_FOLDER_ID')
                if folder_id:
                    spec_clean['folderId'] = folder_id
                    print(f"   Using folder: {folder_id}")
                else:
                    raise Exception(
                        "folderId is required for new workbooks. "
                        "Set 'default_folder_id' in config.yml or SIGMA_FOLDER_ID environment variable."
                    )

            result = client.create_workbook(spec_clean)
            workbook_id = result.get('workbookId')
            print(f"   ✅ Created with ID: {workbook_id}")

        # After create/update, fetch the latest spec from Sigma and write back.
        # On CREATE, Sigma reassigns internal element IDs (layout elementId refs
        # must match the readback), so this write-back is required for round-tripping.
        if workbook_id:
            print(f"   Syncing back from Sigma...")
            try:
                latest_spec = client.get_workbook_spec(workbook_id)
                with open(file_path, 'w') as f:
                    json.dump(latest_spec, f, indent=2)
                print(f"   ✅ Updated local file with Sigma's version "
                      f"(v{latest_spec.get('documentVersion', '?')})")
            except Exception as e:
                print(f"   ⚠️  Could not sync back: {e}")
                spec['workbookId'] = workbook_id
                with open(file_path, 'w') as f:
                    json.dump(spec, f, indent=2)

        if workbook_id:
            if 'workbooks' not in config:
                config['workbooks'] = {}
            existing = config['workbooks'].get(workbook_id, {})
            config['workbooks'][workbook_id] = {
                'file': file_path.name,
                'name': wb_name,
                'path': existing.get('path', ''),
                'last_synced': datetime.utcnow().isoformat() + 'Z'
            }

        return True

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: python sync_workbooks_to_sigma.py <file1.json> [file2.json] ...")
        print("       python sync_workbooks_to_sigma.py --all")
        sys.exit(1)

    if args[0] == '--all':
        wb_dir = Path('workbooks')
        if wb_dir.exists():
            args = [str(f) for f in wb_dir.glob('*.json')]
        else:
            print("No workbooks/ directory found")
            sys.exit(1)

    if not args:
        print("No JSON files to sync")
        sys.exit(0)

    print("=" * 60)
    print("🔄 Sigma Workbook Sync")
    print("=" * 60)

    try:
        client = SigmaClient()
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        sys.exit(1)

    config = load_config()

    success = 0
    failed = 0

    for file_path in args:
        if file_path.endswith('.json') and Path(file_path).name != '_template.json':
            if sync_file(client, file_path, config):
                success += 1
            else:
                failed += 1

    save_config(config)

    print("\n" + "=" * 60)
    print(f"✅ Synced: {success}  ❌ Failed: {failed}")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
