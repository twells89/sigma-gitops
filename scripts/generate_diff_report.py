#!/usr/bin/env python3
"""
Generate a diff report for data model changes in a PR.
Outputs markdown suitable for PR comments.
"""

import json
import subprocess
import sys
from pathlib import Path


def run_git_command(cmd):
    """Run a git command and return output, with error handling."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        print(f"DEBUG: Running: {' '.join(cmd)}", file=sys.stderr)
        print(f"DEBUG: Return code: {result.returncode}", file=sys.stderr)
        print(f"DEBUG: Output: {result.stdout[:200]}", file=sys.stderr)
        return result
    except Exception as e:
        print(f"DEBUG: Git command failed: {e}", file=sys.stderr)
        return None


def get_changed_files():
    """Get list of changed data model files in the PR."""
    print("DEBUG: Looking for changed files...", file=sys.stderr)
    
    # Try different git diff strategies
    strategies = [
        # PR context - compare PR head to base
        ['git', 'diff', '--name-only', 'origin/main...HEAD'],
        ['git', 'diff', '--name-only', 'origin/main', 'HEAD'],
        # Simple HEAD comparison
        ['git', 'diff', '--name-only', 'HEAD^', 'HEAD'],
        ['git', 'diff', '--name-only', 'HEAD~1', 'HEAD'],
    ]
    
    for cmd in strategies:
        result = run_git_command(cmd)
        if result and result.returncode == 0:
            all_files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
            # Filter for data-models/*.json
            json_files = [f for f in all_files if f.startswith('data-models/') and f.endswith('.json')]
            if json_files:
                print(f"DEBUG: Found files with strategy: {json_files}", file=sys.stderr)
                return json_files
    
    print("DEBUG: No changed files found with any strategy", file=sys.stderr)
    return []


def get_file_at_ref(file_path, ref):
    """Get file contents at a specific git ref."""
    print(f"DEBUG: Getting {file_path} at {ref}", file=sys.stderr)
    try:
        result = subprocess.run(
            ['git', 'show', f'{ref}:{file_path}'],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"DEBUG: Could not get file at {ref}: {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"DEBUG: Error getting file: {e}", file=sys.stderr)
    return None


def get_simple_diff(old_spec, new_spec):
    """Get a simple list of changed top-level fields."""
    if not old_spec:
        return [f"✨ New file created with {len(json.dumps(new_spec))} characters"]
    
    changes = []
    
    # Compare top-level fields
    all_keys = set(old_spec.keys()) | set(new_spec.keys())
    
    for key in sorted(all_keys):
        old_val = old_spec.get(key)
        new_val = new_spec.get(key)
        
        if old_val != new_val:
            if key not in old_spec:
                changes.append(f"➕ Added field: `{key}`")
            elif key not in new_spec:
                changes.append(f"➖ Removed field: `{key}`")
            else:
                # Field modified
                if isinstance(old_val, (dict, list)):
                    old_len = len(json.dumps(old_val))
                    new_len = len(json.dumps(new_val))
                    if old_len != new_len:
                        changes.append(f"🔄 Modified `{key}`: {old_len} → {new_len} characters")
                elif isinstance(old_val, str) and len(str(old_val)) > 50:
                    changes.append(f"🔄 Modified `{key}`: {len(str(old_val))} → {len(str(new_val))} characters")
                else:
                    changes.append(f"🔄 Modified `{key}`: `{old_val}` → `{new_val}`")
    
    return changes


def compare_columns(old_cols, new_cols):
    """Compare column lists and return changes."""
    # Use column ID as the key if available, otherwise name
    old_dict = {}
    for c in (old_cols or []):
        key = c.get('id') or c.get('name', '')
        old_dict[key] = c
    
    new_dict = {}
    for c in (new_cols or []):
        key = c.get('id') or c.get('name', '')
        new_dict[key] = c
    
    added = set(new_dict.keys()) - set(old_dict.keys())
    removed = set(old_dict.keys()) - set(new_dict.keys())
    
    modified = []
    renamed = []
    
    for key in set(old_dict.keys()) & set(new_dict.keys()):
        old_col = old_dict[key]
        new_col = new_dict[key]
        
        # Check if column was renamed (same ID, different name)
        old_name = old_col.get('name', '')
        new_name = new_col.get('name', '')
        if old_name != new_name and old_col.get('id') == new_col.get('id'):
            renamed.append((old_name, new_name))
        
        # Check if anything else changed (formula, type, etc.)
        if old_col != new_col:
            modified.append(new_name or new_col.get('id', key))
    
    return added, removed, modified, renamed


def analyze_changes(old_spec, new_spec):
    """Analyze changes between two specs."""
    changes = []
    
    if not old_spec:
        changes.append("✨ **New data model**")
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
    
    # Description change
    if old_spec.get('description') != new_spec.get('description'):
        old_desc = old_spec.get('description', '')
        new_desc = new_spec.get('description', '')
        if not old_desc and new_desc:
            changes.append(f"➕ Added description: _{new_desc[:100]}_")
        elif old_desc and not new_desc:
            changes.append(f"➖ Removed description")
        else:
            changes.append(f"🔄 Modified description")
    
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
        
        # Check if page name changed
        old_page_name = old_page.get('name', 'Unnamed')
        new_page_name = new_page.get('name', 'Unnamed')
        if old_page_name != new_page_name:
            changes.append(f"📝 Renamed page: `{old_page_name}` → `{new_page_name}`")
        
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
            
            old_elem_name = old_elem.get('name', 'Unnamed')
            new_elem_name = new_elem.get('name', 'Unnamed')
            
            # Check if element name changed
            if old_elem_name != new_elem_name:
                elem_kind = new_elem.get('kind', 'element')
                changes.append(f"📝 Renamed {elem_kind}: `{old_elem_name}` → `{new_elem_name}`")
            
            added, removed, modified, renamed = compare_columns(
                old_elem.get('columns', []),
                new_elem.get('columns', [])
            )
            
            elem_name = new_elem_name
            
            if renamed:
                for old_name, new_name in renamed:
                    changes.append(f"  📝 `{elem_name}`: Renamed column: `{old_name}` → `{new_name}`")
            if added:
                changes.append(f"  ➕ `{elem_name}`: Added columns: {', '.join(f'`{c}`' for c in list(added)[:5])}")
            if removed:
                changes.append(f"  ➖ `{elem_name}`: Removed columns: {', '.join(f'`{c}`' for c in list(removed)[:5])}")
            if modified:
                changes.append(f"  🔄 `{elem_name}`: Modified columns: {', '.join(f'`{c}`' for c in list(modified)[:5])}")
    
    return changes


def main():
    print("DEBUG: Starting diff report generation", file=sys.stderr)
    
    changed_files = get_changed_files()
    
    if not changed_files:
        print("DEBUG: No changed files detected", file=sys.stderr)
        print("No data model changes detected.")
        return
    
    print(f"**{len(changed_files)} data model file(s) changed:**\n")
    
    for file_path in changed_files:
        if not file_path:
            continue
        
        print(f"DEBUG: Processing {file_path}", file=sys.stderr)
        file_name = Path(file_path).name
        print(f"### 📄 `{file_name}`\n")
        
        old_spec = get_file_at_ref(file_path, 'origin/main')
        if not old_spec:
            # Try alternative ref
            old_spec = get_file_at_ref(file_path, 'HEAD^')
        
        try:
            with open(file_path) as f:
                new_spec = json.load(f)
        except Exception as e:
            print(f"⚠️ Could not parse JSON: {e}\n")
            print(f"DEBUG: JSON parse error: {e}", file=sys.stderr)
            continue
        
        # Try detailed analysis first
        changes = analyze_changes(old_spec, new_spec)
        
        # Fallback to simple diff if no changes detected but files are different
        if not changes and old_spec != new_spec:
            print("DEBUG: Using simple diff fallback", file=sys.stderr)
            changes = get_simple_diff(old_spec, new_spec)
        
        if changes:
            for change in changes:
                print(change)
        else:
            print("_No structural changes detected (version numbers may have changed)_")
        
        print()


if __name__ == '__main__':
    main()
