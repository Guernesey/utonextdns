# UT1 Toulouse → NextDNS Blocklists

This repository automatically converts UT1 (Université Toulouse 1 Capitole) blacklist categories into NextDNS-compatible blocklists.

## Contents

- `update_lists.py`: downloads the UT1 archive, cleans domain entries, and generates `dist/*.txt` plus `metadata.json`.
- `dist/toulouse-<category>.txt`: per-category blocklists.
- `dist/toulouse-bundle-<id>.txt`: grouped blocklists generated from multiple categories.
- `metadata.json`: generated manifest containing published list IDs, names, descriptions, raw URLs, and entry counts.
- `ut1<id>.json` (example: `ut1adult.json`): NextDNS-compatible metadata file generated per published list.

## Requirements

- Python 3.10+
- Python dependency: `requests`

Quick installation:

```bash
python -m pip install requests
```

## Local Usage

1. (Optional) Define environment variables to generate correct GitHub raw URLs in `metadata.json`:

```bash
export GITHUB_OWNER="<your-user-or-org>"
export GITHUB_REPO="<your-repo>"
export GITHUB_BRANCH="main"
```

2. (Optional) Create a `.env` file with grouped lists to publish in `metadata.json`:

```dotenv
Adult:adult,agressif,drogue,lingerie,sexual_education,dating,celebrity
Actuality:press,radio,fakenews,sports
Financial:financial,bitcoin,shopping,cryptojacking
```

- Group format: `<ListName>:<category1>,<category2>,...` (one line per list).
- When at least one group line is present, `metadata.json` is built from grouped lists.
- Per-category files are always generated for every available UT1 category.
- Backward-compatible fallback: if no group lines are defined, `CATEGORIES_TO_PUSH` can still filter category-based metadata.

3. Run:

```bash
python update_lists.py
```

## Add a List to NextDNS

1. Open your NextDNS dashboard.
2. Go to **Privacy**.
3. In **Add a custom filter**, paste the raw URL of the desired list (see `metadata.json`).
4. Confirm the addition.

Expected raw URL format:

```text
https://raw.githubusercontent.com/<owner>/<repo>/main/dist/toulouse-bundle-adult.txt
```

## Automation

The GitHub Actions workflow (`.github/workflows/update.yml`) runs every Monday at 00:00 (UTC), then automatically commits and pushes updated files.

## License and Credits

- **Source data**: The original blacklist data is provided by Université Toulouse 1 Capitole (UT1) and distributed under the **Licence Ouverte Etalab 2.0**.
- **Repository code**: The code in this repository is released under the **MIT License**.
