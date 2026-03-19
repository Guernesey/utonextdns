# UT1 Toulouse → NextDNS Blocklists

This repository automatically converts UT1 (Université Toulouse 1 Capitole) blacklist categories into NextDNS-compatible blocklists.

## Contents

- `update_lists.py`: downloads the UT1 archive, cleans domain entries, and generates `dist/*.txt` plus `ut1<id>` metadata.
- `dist/UT1-<group>.txt`: grouped blocklists generated from multiple categories (large ones are automatically split into `...-1.txt`, `...-2.txt`, etc.).
- `ut1<id>.json` (example: `ut1adult.json`): NextDNS-compatible metadata file generated per published list.

## Requirements

- Python 3.10+
- Python dependencies: `requests`, `PyYAML`

Quick installation:

```bash
python -m pip install requests pyyaml
```

## Local Usage

1. Edit the existing `.env` file (already committed in the repo). It is written in **YAML** and serves as the canonical template for defining bundles and GitHub metadata. Adjust the sections to match the categories you want to publish—no separate example is required.

- `bundles` describes every published list (comma-separated strings or true YAML lists are both supported). At least one entry is required.
- Per-category files are always generated for every available UT1 category.
- When a bundle exceeds ~70 MB, it is automatically split into multiple `UT1-<group>-N.txt` files. The corresponding `ut1<id>.json` entries follow the "UT1 bundle <group>" naming convention and list every part so you can add them all in NextDNS.

2. Run:

```bash
python update_lists.py
```

## Automation

The GitHub Actions workflow (`.github/workflows/update.yml`) runs every day, then automatically commits and pushes updated files.

## Credits and License

This project uses the blacklists provided by **Université Toulouse Capitole (UT1)**.

- **Source:** [https://dsi.ut-capitole.fr/blacklists/](https://dsi.ut-capitole.fr/blacklists/)
- **License:** These lists are distributed under a [Creative Commons License](https://creativecommons.org/).
- **Maintainer:** Fabrice Prigent (Toulouse 1 University).
