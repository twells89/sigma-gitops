#!/usr/bin/env python3
"""
Pull/export data models from Sigma to local JSON files.

Usage:
    python pull_from_sigma.py              # Pull all data models
    python pull_from_sigma.py --id UUID    # Pull specific data model by ID
    python pull_from_sigma.py --name NAME  # Pull specific data model by name
    
Environment variables:
    SIGMA_CLIENT_ID - API client ID
    SIGMA_SECRET - API client secret  
    SIGMA_CLOUD - Cloud provider (aws, azure, gcp) - defaults to aws
"""

import os
import sys
import json
import yaml
import argparse
import requests
from pathlib import Path
from datetime import datetime

# API base URLs by cloud
CLOUD_URLS = {
    'aws': 'https://aws-api.sigmacomputing.com',
    'azure': 'https://api.us.azure.sigmacomputing.com',
    'gcp': 'https://api.sigmacomputing.com'
}


class SigmaClient:
    def __init__(self):
        self.client_id = os.environ.get('SIGMA_CLIENT_ID')
        self.client_secret = os.environ.get('SIGMA_SECRET')
        self.cloud = os.environ.get('SIGMA_CLOUD', 'aws').lower()
        
        if not self.client_id or not self.client_secret:
            raise ValueError("SIGMA_CLIENT_ID and SIGMA_SECRET environment variables required")
        
        self.base_url = CLOUD_URLS.get(self.cloud)
        if not self.base_url:
            raise ValueError(f"Invalid SIGMA_CLOUD: {self.cloud}. Use: aws, azure, or gcp")
        
        self.access_token = None
        self._authenticate()
    
    def _authenticate(self):
        print(f"🔐 Authenticating with Sigma ({self.cloud})...")
        
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
        print("✓ Authenticated successfully")
    
    def _headers(self):
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
    
    def list_data_models(self):
        response = requests.get(
            f"{self.base_url}/v2/datamodels",
            headers=self._headers()
        )
        
        if response.status_code != 200:
            raise Exception(f"Failed to list data models: {response.text}")
        
        return response.json().get('entries', [])
    
    def get_data_model_spec(self, data_model_id):
        response = requests.get(
            f"{self.base_url}/v3alpha/datamodels/{data_model_id}/spec",
            headers=self._headers()
        )
        
        if response.status_code != 200:
            raise Exception(f"Failed to get data model spec: {response.text}")
        
        return response.json()


def sanitize_filename(name):
    """Convert name to a safe filename."""
    return name.lower().replace(' ', '-').replace('_', '-').\
        encode('ascii', 'ignore').decode().\
        replace('--', '-').strip('-')[:50] or 'unnamed'


def load_config():
    config_path = Path('config.yml')
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config):
    with open('config.yml', 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def pull_data_model(client, data_model_id, output_dir, config):
    """Pull a single data model and save to file."""
    print(f"\n📥 Pulling data model: {data_model_id}")
    
    try:
        spec = client.get_data_model_spec(data_model_id)
        
        model_name = spec.get('name', 'unnamed')
        file_name = sanitize_filename(model_name) + '.json'
        file_path = output_dir / file_name
        
        # Save JSON file
        with open(file_path, 'w') as f:
            json.dump(spec, f, indent=2)
        
        print(f"   ✓ Saved: {file_path}")
        
        # Update config
        if 'data_models' not in config:
            config['data_models'] = {}
        
        config['data_models'][data_model_id] = {
            'file': file_name,
            'name': model_name,
            'last_pulled': datetime.utcnow().isoformat() + 'Z'
        }
        
        return True
        
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Pull data models from Sigma')
    parser.add_argument('--id', help='Pull specific data model by ID')
    parser.add_argument('--name', help='Pull specific data model by name')
    parser.add_argument('--output', default='data-models', help='Output directory')
    args = parser.parse_args()
    
    print("=" * 60)
    print("📥 Sigma Data Model Pull")
    print("=" * 60)
    
    # Initialize client
    try:
        client = SigmaClient()
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        sys.exit(1)
    
    # Setup output directory
    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)
    
    # Load config
    config = load_config()
    
    # Get data models to pull
    if args.id:
        data_model_ids = [args.id]
    elif args.name:
        # Find by name
        print("\n📋 Searching for data model by name...")
        models = client.list_data_models()
        matches = [m for m in models if m.get('name', '').lower() == args.name.lower()]
        if not matches:
            print(f"❌ No data model found with name: {args.name}")
            sys.exit(1)
        data_model_ids = [m['dataModelId'] for m in matches]
    else:
        # Pull all
        print("\n📋 Fetching all data models...")
        models = client.list_data_models()
        data_model_ids = [m['dataModelId'] for m in models]
        print(f"   Found {len(data_model_ids)} data models")
    
    # Pull each data model
    success = 0
    failed = 0
    
    for dm_id in data_model_ids:
        if pull_data_model(client, dm_id, output_dir, config):
            success += 1
        else:
            failed += 1
    
    # Save config
    save_config(config)
    
    # Summary
    print("\n" + "=" * 60)
    print(f"✓ Pulled: {success}  ✗ Failed: {failed}")
    print(f"📁 Output: {output_dir}/")
    print("=" * 60)
    
    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
