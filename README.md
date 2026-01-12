# Sigma Data Models

Manage Sigma Computing data models as code with version control, code review, and automated deployment.

## Overview

```
┌─────────────────┐     Pull      ┌─────────────────┐
│     Sigma       │ ────────────► │     GitHub      │
│   Computing     │               │   Repository    │
│                 │ ◄──────────── │                 │
└─────────────────┘  Push/Merge   └─────────────────┘
                          │
                          │ PR + Code Review
                          ▼
                    ┌───────────┐
                    │  CI/CD    │
                    │ Automated │
                    │   Sync    │
                    └───────────┘
```

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/YOUR_ORG/sigma-data-models.git
cd sigma-data-models
pip install requests pyyaml
```

### 2. Configure Environment

```bash
export SIGMA_CLIENT_ID="your-client-id"
export SIGMA_SECRET="your-client-secret"
export SIGMA_CLOUD="aws"  # or azure, gcp
```

### 3. Pull Existing Data Models

```bash
python scripts/pull_from_sigma.py
```

### 4. Push Changes to Sigma

```bash
python scripts/sync_to_sigma.py data-models/my-model.json
```

## Repository Structure

```
├── .github/
│   └── workflows/
│       └── sync-to-sigma.yml    # GitHub Actions workflow
├── data-models/
│   ├── sales-model.json         # Data model JSON specs
│   ├── inventory-model.json
│   └── ...
├── scripts/
│   ├── sync_to_sigma.py         # Push changes to Sigma
│   ├── pull_from_sigma.py       # Pull from Sigma
│   └── generate_diff_report.py  # PR diff reports
├── config.yml                    # ID mappings & settings
└── README.md
```

## GitHub Actions Setup

### Required Secrets

Add these in your repository settings (Settings → Secrets and variables → Actions):

| Secret | Description |
|--------|-------------|
| `SIGMA_CLIENT_ID` | Your Sigma API client ID |
| `SIGMA_SECRET` | Your Sigma API client secret |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SIGMA_CLOUD` | Cloud provider (aws, azure, gcp) | `aws` |

### Workflow Triggers

The workflow runs automatically when:
- **Push to main**: Syncs changed data models to Sigma
- **Pull Request**: Validates JSON and posts a diff report comment
- **Manual trigger**: Use "Run workflow" in Actions tab

## Workflow

### Making Changes

1. **Create a branch**
   ```bash
   git checkout -b feature/add-revenue-metrics
   ```

2. **Edit data model JSON**
   ```bash
   # Edit existing model
   vim data-models/sales-model.json
   
   # Or create new model
   cp data-models/_template.json data-models/new-model.json
   ```

3. **Commit and push**
   ```bash
   git add data-models/
   git commit -m "Add revenue metrics to sales model"
   git push origin feature/add-revenue-metrics
   ```

4. **Open PR for review**
   - The workflow validates JSON syntax
   - A diff report is posted as a PR comment
   - Team can review changes before merge

5. **Merge to deploy**
   - On merge to main, changes are automatically synced to Sigma

### Pulling Updates from Sigma UI

If changes are made directly in the Sigma UI:

```bash
# Pull all data models
python scripts/pull_from_sigma.py

# Pull specific model
python scripts/pull_from_sigma.py --name "Sales Model"

# Commit the updates
git add data-models/ config.yml
git commit -m "Sync changes from Sigma UI"
git push
```

## Data Model JSON Structure

```json
{
  "dataModelId": "uuid-assigned-by-sigma",
  "name": "Sales Model",
  "schemaVersion": 1,
  "pages": [
    {
      "id": "page-uuid",
      "name": "Main",
      "elements": [
        {
          "id": "element-uuid",
          "name": "Sales Table",
          "kind": "table",
          "source": {
            "connectionId": "connection-uuid",
            "kind": "warehouse-table",
            "path": ["DATABASE", "SCHEMA", "TABLE"]
          },
          "columns": [
            {
              "id": "col-uuid",
              "name": "Revenue",
              "formula": "[Sales/Amount]"
            }
          ]
        }
      ]
    }
  ]
}
```

### Supported Elements

- Tables
- Calculated columns
- Metrics
- Relationships
- Custom SQL elements
- Controls (List, Checkbox, Date, Number, Text, Range)
- Filters (List, Top N, Number range, Date range, Text)

## Local Development

### Validate JSON

```bash
# Validate all files
for f in data-models/*.json; do python3 -m json.tool "$f" > /dev/null && echo "✓ $f"; done
```

### Test Sync (Dry Run)

```bash
# See what would be synced without making changes
python scripts/sync_to_sigma.py --dry-run data-models/*.json
```

### Force Re-sync

```bash
# Re-sync all data models (even if unchanged)
python scripts/sync_to_sigma.py --all
```

## Troubleshooting

### Authentication Errors

```
Authentication failed: unauthorized
```

- Verify `SIGMA_CLIENT_ID` and `SIGMA_SECRET` are correct
- Ensure the API credentials haven't expired
- Check that your account has "Create, edit, and publish datasets" permission

### Data Model Not Found

```
Failed to update data model: not found
```

- The `dataModelId` in the JSON may be stale
- Remove the `dataModelId` field to create a new model
- Or update `config.yml` with the correct mapping

### Schema Version Mismatch

```
Failed to update: schema version mismatch
```

- Pull the latest from Sigma: `python scripts/pull_from_sigma.py --id <uuid>`
- Merge your changes with the updated spec
- Push again

## API Reference

This repo uses these Sigma API endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v2/auth/token` | POST | Get access token |
| `/v2/datamodels` | GET | List data models |
| `/v3alpha/datamodels/{id}/spec` | GET | Get JSON representation |
| `/v3alpha/datamodels/{id}/spec` | PUT | Update data model |
| `/v3alpha/datamodels/spec` | POST | Create data model |

See [Sigma API Docs](https://help.sigmacomputing.com/reference/create-and-manage-data-models-from-code) for details.

## License

MIT
