#!/usr/bin/env python3
"""
Sync data model JSON files to Sigma Computing via the API.

Usage:
    python sync_to_sigma.py data-models/sales-model.json data-models/inventory-model.json
    python sync_to_sigma.py --all
    
Environment variables:
    SIGMA_CLIENT_ID - API client ID
    SIGMA_SECRET - API client secret  
    SIGMA_CLOUD - Cloud provider (aws, azure, gcp) - defaults to aws
"""

import os
import sys
import json
import yaml
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
        """Get access token from Sigma API."""
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
        """Get all data models from Sigma."""
        response = requests.get(
            f"{self.base_url}/v2/datamodels",
            headers=self._headers()
        )
        
        if response.status_code != 200:
            raise Exception(f"Failed to list data models: {response.text}")
        
        return response.json().get('entries', [])
    
    def get_data_model_spec(self, data_model_id):
        """Get the JSON representation of a data model."""
        response = requests.get(
            f"{self.base_url}/v3alpha/datamodels/{data_model_id}/spec",
            headers=self._headers()
        )
        
        if response.status_code != 200:
            raise Exception(f"Failed to get data model spec: {response.text}")
        
        return response.json()
    
    def create_data_model(self, spec):
        """Create a new data model from a JSON spec."""
        response = requests.post(
            f"{self.base_url}/v3alpha/datamodels/spec",
            headers=self._headers(),
            json=spec
        )
        
        if response.status_code not in [200, 201]:
            raise Exception(f"Failed to create data model: {response.text}")
        
        return response.json()
    
    def update_data_model(self, data_model_id, spec):
        """Update an existing data model from a JSON spec."""
        response = requests.put(
            f"{self.base_url}/v3alpha/datamodels/{data_model_id}/spec",
            headers=self._headers(),
            json=spec
        )
        
        if response.status_code != 200:
            raise Exception(f"Failed to update data model: {response.text}")
        
        return response.json()


def load_config():
    """Load the config.yml file."""
    config_path = Path('config.yml')
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config):
    """Save the config.yml file."""
    with open('config.yml', 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def get_data_model_id_for_file(file_path, config):
    """Look up the Sigma data model ID for a file path."""
    file_name = Path(file_path).name
    
    # Check config for mapping
    mappings = config.get('data_models', {})
    for dm_id, info in mappings.items():
        if info.get('file') == file_name:
            return dm_id
    
    return None


def sync_file(client, file_path, config):
    """Sync a single data model file to Sigma."""
    file_path = Path(file_path)
    
    if not file_path.exists():
        print(f"⚠️  File not found: {file_path}")
        return False
    
    print(f"\n📄 Processing: {file_path.name}")
    
    # Load the JSON spec
    with open(file_path) as f:
        spec = json.load(f)
    
    model_name = spec.get('name', file_path.stem)
    
    # Check if we have an existing ID mapping
    data_model_id = get_data_model_id_for_file(file_path, config)
    
    # Also check if the spec itself contains an ID
    if not data_model_id and spec.get('dataModelId'):
        data_model_id = spec['dataModelId']
    
    try:
        if data_model_id:
            # Update existing
            print(f"   Updating data model: {data_model_id}")
            result = client.update_data_model(data_model_id, spec)
            print(f"   ✓ Updated: {model_name}")
        else:
            # Create new
            print(f"   Creating new data model: {model_name}")
            
            # Remove any stale IDs from the spec for creation
            spec_clean = {k: v for k, v in spec.items() 
                        if k not in ['dataModelId', 'ownerId', 'createdBy', 'updatedBy', 
                                    'createdAt', 'updatedAt', 'documentVersion', 
                                    'latestDocumentVersion', 'url']}
            
            # Ensure schemaVersion is an integer
            if 'schemaVersion' in spec_clean:
                if isinstance(spec_clean['schemaVersion'], str):
                    # Convert "v1" or "1" to integer 1
                    spec_clean['schemaVersion'] = int(spec_clean['schemaVersion'].replace('v', ''))
            else:
                spec_clean['schemaVersion'] = 1
            
            # Add folderId if not present (required for creation)
            if 'folderId' not in spec_clean:
                # Try to get from config
                folder_id = config.get('default_folder_id') or os.environ.get('SIGMA_FOLDER_ID')
                if folder_id:
                    spec_clean['folderId'] = folder_id
                    print(f"   Using folder: {folder_id}")
                else:
                    raise Exception(
                        "folderId is required for new data models. "
                        "Set 'default_folder_id' in config.yml or SIGMA_FOLDER_ID environment variable. "
                        "Find your folder ID in Sigma: open a folder, check the URL for the ID after /folder/"
                    )
            
            result = client.create_data_model(spec_clean)
            data_model_id = result.get('dataModelId')
            print(f"   ✓ Created with ID: {data_model_id}")
        
        # After create/update, fetch the latest spec from Sigma and write back
        # This keeps GitHub in sync with Sigma's version numbers
        if data_model_id:
            print(f"   Syncing back from Sigma...")
            try:
                latest_spec = client.get_data_model_spec(data_model_id)
                with open(file_path, 'w') as f:
                    json.dump(latest_spec, f, indent=2)
                print(f"   ✓ Updated local file with Sigma's version (v{latest_spec.get('documentVersion', '?')})")
            except Exception as e:
                print(f"   ⚠️  Could not sync back: {e}")
                # Fall back to just adding the ID
                spec['dataModelId'] = data_model_id
                with open(file_path, 'w') as f:
                    json.dump(spec, f, indent=2)
        
        # Update config with the mapping
        if data_model_id:
            if 'data_models' not in config:
                config['data_models'] = {}
            
            config['data_models'][data_model_id] = {
                'file': file_path.name,
                'name': model_name,
                'last_synced': datetime.utcnow().isoformat() + 'Z'
            }
        
        return True
        
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False


def main():
    args = sys.argv[1:]
    
    if not args:
        print("Usage: python sync_to_sigma.py <file1.json> [file2.json] ...")
        print("       python sync_to_sigma.py --all")
        sys.exit(1)
    
    # Handle --all flag
    if args[0] == '--all':
        data_models_dir = Path('data-models')
        if data_models_dir.exists():
            args = [str(f) for f in data_models_dir.glob('*.json')]
        else:
            print("No data-models/ directory found")
            sys.exit(1)
    
    if not args:
        print("No JSON files to sync")
        sys.exit(0)
    
    print("=" * 60)
    print("🔄 Sigma Data Model Sync")
    print("=" * 60)
    
    # Initialize client
    try:
        client = SigmaClient()
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        sys.exit(1)
    
    # Load config
    config = load_config()
    
    # Sync each file
    success = 0
    failed = 0
    
    for file_path in args:
        if file_path.endswith('.json'):
            if sync_file(client, file_path, config):
                success += 1
            else:
                failed += 1
    
    # Save updated config
    save_config(config)
    
    # Summary
    print("\n" + "=" * 60)
    print(f"✓ Synced: {success}  ✗ Failed: {failed}")
    print("=" * 60)
    
    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
