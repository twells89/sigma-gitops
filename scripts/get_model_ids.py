#!/usr/bin/env python3
"""
Map changed data model filenames to their Sigma model IDs using config.yml.

Usage:
    python scripts/get_model_ids.py order-analysis.json retail-analytics.json
    python scripts/get_model_ids.py --all

Output:
    Space-separated list of model IDs. Empty string if none found.
"""
import sys
import yaml
from pathlib import Path


def load_config():
    config_path = Path('config.yml')
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def main():
    args = sys.argv[1:]
    if not args:
        print('', end='')
        return

    config = load_config()
    mappings = config.get('data_models', {})

    if args[0] == '--all':
        # Return all known model IDs
        print(' '.join(mappings.keys()), end='')
        return

    # Build reverse map: filename → model ID
    file_to_id = {}
    for model_id, info in mappings.items():
        if isinstance(info, dict) and info.get('file'):
            file_to_id[info['file']] = model_id

    model_ids = []
    for arg in args:
        filename = Path(arg).name
        model_id = file_to_id.get(filename)
        if model_id:
            model_ids.append(model_id)
        else:
            print(f"Warning: no model ID found for {filename} (not yet synced?)", file=sys.stderr)

    print(' '.join(model_ids), end='')


if __name__ == '__main__':
    main()
