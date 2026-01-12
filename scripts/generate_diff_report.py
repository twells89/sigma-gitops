#!/usr/bin/env python3
"""
Generate a diff report for data model changes in a PR.
Outputs markdown suitable for PR comments.
"""

import json
import subprocess
from pathlib import Path


def get_changed_files():
    """Get list of changed data model files in the PR."""
    import os
    
    # Try different git diff strategies
    strategies = [
        ['git', 'diff', '--name-only', 'origin/main...HEAD', '--', 'data-models/*.json'],
        ['git', 'diff', '--name-only', 'origin/main', 'HEAD', '--', 'data-models/*.json'],
        ['git', 'diff', '--name-only', 'HEAD~1', 'HEAD', '--', 'data-models/*.json'],
    ]
    
    for cmd in strategies:
        result = subprocess.run(cmd, capture_output=True, text=True)
        files = [f for f in result.stdout.strip().split('\n') if f and f.endswith('.json')]
        if files:
            return files
    
    # Fallback: check GITHUB_EVENT_PATH for PR file list
    event_path = os.environ.get('GITHUB_EVENT_PATH')
    if event_path:
        try:
            with open(event_path) as f:
                import json as json_mod
                event = json_mod.load(f)
                # For PRs, we can't get file list from event, but we tried git
        except:
            pass
    
    return []


def get_file_at_ref(file_path, ref):
    """Get file contents at a specific git ref."""
    try:
        result = subprocess.run(
            ['git', 'show', f'{ref}:{file_path}'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except:
        pass
    return None


def compare_columns(old_cols, new_cols):
    """Compare column lists and return changes."""
    old_names = {c.get('name', c.get('id', '')): c for c in (old_cols or [])}
    new_names = {c.get('name', c.get('id', '')): c for c in (new_cols or [])}
    
    added = set(new_names.keys()) - set(old_names.keys())
    removed = set(old_names.keys()) - set(new_names.keys())
    
    modified = []
    for name in set(old_names.keys()) & set(new_names.keys()):
        old_formula = old_names[name].get('formula', '')
        new_formula = new_names[name].get('formula', '')
        if old_formula != new_formula:
            modified.append(name)
    
    return added, removed, modified


def analyze_changes(old_spec, new_spec):
    """Analyze changes between two specs."""
    changes = []
    
    if not old_spec:
        changes.append("🆕 **New data model**")
        if new_spec.get('name'):
            changes.append(f"- Name: `{new_spec['name']}`")
        
        # Count elements
        for page in new_spec.get('pages', []):
            for element in page.get('elements', []):
                elem_name = element.get('name', 'Unnamed')
                elem_kind = element.get('kind', 'unknown')
                col_count = len(element.get('columns', []))
                changes.append(f"- {elem_kind}: `{elem_name}` ({col_count} columns)")
        
        return changes
    
    # Name change
    if old_spec.get('name') != new_spec.get('name'):
        changes.append(f"📝 Name: `{old_spec.get('name')}` → `{new_spec.get('name')}`")
    
    # Compare pages and elements
    old_pages = {p.get('id'): p for p in old_spec.get('pages', [])}
    new_pages = {p.get('id'): p for p in new_spec.get('pages', [])}
    
    # New pages
    for page_id in set(new_pages.keys()) - set(old_pages.keys()):
        page = new_pages[page_id]
        changes.append(f"➕ New page: `{page.get('name', 'Unnamed')}`")
    
    # Removed pages
    for page_id in set(old_pages.keys()) - set(new_pages.keys()):
        page = old_pages[page_id]
        changes.append(f"➖ Removed page: `{page.get('name', 'Unnamed')}`")
    
    # Modified pages
    for page_id in set(old_pages.keys()) & set(new_pages.keys()):
        old_page = old_pages[page_id]
        new_page = new_pages[page_id]
        
        old_elements = {e.get('id'): e for e in old_page.get('elements', [])}
        new_elements = {e.get('id'): e for e in new_page.get('elements', [])}
        
        # New elements
        for elem_id in set(new_elements.keys()) - set(old_elements.keys()):
            elem = new_elements[elem_id]
            changes.append(f"➕ New {elem.get('kind', 'element')}: `{elem.get('name', 'Unnamed')}`")
        
        # Removed elements
        for elem_id in set(old_elements.keys()) - set(new_elements.keys()):
            elem = old_elements[elem_id]
            changes.append(f"➖ Removed {elem.get('kind', 'element')}: `{elem.get('name', 'Unnamed')}`")
        
        # Modified elements
        for elem_id in set(old_elements.keys()) & set(new_elements.keys()):
            old_elem = old_elements[elem_id]
            new_elem = new_elements[elem_id]
            
            added, removed, modified = compare_columns(
                old_elem.get('columns', []),
                new_elem.get('columns', [])
            )
            
            elem_name = new_elem.get('name', 'Unnamed')
            
            if added:
                changes.append(f"  ➕ `{elem_name}`: Added columns: {', '.join(f'`{c}`' for c in added)}")
            if removed:
                changes.append(f"  ➖ `{elem_name}`: Removed columns: {', '.join(f'`{c}`' for c in removed)}")
            if modified:
                changes.append(f"  📝 `{elem_name}`: Modified columns: {', '.join(f'`{c}`' for c in modified)}")
    
    return changes


def main():
    changed_files = get_changed_files()
    
    if not changed_files:
        print("No data model changes detected.")
        return
    
    print(f"**{len(changed_files)} data model(s) changed:**\n")
    
    for file_path in changed_files:
        if not file_path:
            continue
            
        file_name = Path(file_path).name
        print(f"### `{file_name}`\n")
        
        old_spec = get_file_at_ref(file_path, 'origin/main')
        
        try:
            with open(file_path) as f:
                new_spec = json.load(f)
        except:
            print("⚠️ Could not parse JSON\n")
            continue
        
        changes = analyze_changes(old_spec, new_spec)
        
        if changes:
            for change in changes:
                print(change)
        else:
            print("_No structural changes detected_")
        
        print()


if __name__ == '__main__':
    main()
