#!/usr/bin/env python3
from __future__ import annotations

import io
import ipaddress
import heapq
import json
import os
import re
import resource
import shutil
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import requests
import yaml

SOURCE_URL = "https://dsi.ut-capitole.fr/blacklists/download/blacklists.tar.gz"
DIST_DIR = Path("dist")
TMP_DIR = Path("tmp")
CATEGORY_WORK_DIR = TMP_DIR / "categories"
ARCHIVE_PATH = TMP_DIR / "blacklists.tar.gz"
METADATA_PATH = Path("metadata.json")
CONFIG_PATH = Path(".env")
MAX_BUNDLE_BYTES = 70 * 1024 * 1024
FILE_PREFIX = "UT1-"

DOMAIN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


def ensure_directories() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CATEGORY_WORK_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_previous_outputs() -> None:
    for output_file in DIST_DIR.glob(f"{FILE_PREFIX}*.txt"):
        output_file.unlink(missing_ok=True)
    for metadata_file in Path(".").glob("ut1*.json"):
        metadata_file.unlink(missing_ok=True)


def cleanup_temporary_files() -> None:
    ARCHIVE_PATH.unlink(missing_ok=True)
    try:
        TMP_DIR.rmdir()
    except OSError:
        pass


def load_config(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise RuntimeError(
            f"Configuration file '{path}' not found. Please create it using the YAML format described in README.md."
        )

    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, Mapping):
        raise RuntimeError("Configuration root must be a YAML mapping (dictionary).")

    return data


def apply_env_overrides_from_config(config: Mapping[str, Any]) -> None:
    def set_env_if_missing(env_key: str, value: Any) -> None:
        if value is None or env_key in os.environ:
            return
        os.environ[env_key] = str(value)

    github_cfg = config.get("github")
    if isinstance(github_cfg, Mapping):
        set_env_if_missing("GITHUB_OWNER", github_cfg.get("owner"))
        set_env_if_missing("GITHUB_REPO", github_cfg.get("repo"))
        set_env_if_missing("GITHUB_BRANCH", github_cfg.get("branch"))


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def print_performance_summary(start_time: float) -> None:
    elapsed = time.perf_counter() - start_time
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_time = usage.ru_utime + usage.ru_stime
    max_rss_mb = usage.ru_maxrss / 1024 if os.name == "posix" else usage.ru_maxrss
    log(
        "Run summary: "
        f"duration={elapsed:.2f}s, cpu_time={cpu_time:.2f}s, "
        f"max_rss={max_rss_mb:.1f}MB"
    )


def download_archive(url: str, output_path: Path) -> None:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def is_valid_domain(value: str) -> bool:
    candidate = value.strip().lower().rstrip(".")

    if not candidate:
        return False

    if "://" in candidate or "/" in candidate:
        return False

    if candidate.startswith("http"):
        return False

    try:
        ipaddress.ip_address(candidate)
        return False
    except ValueError:
        pass

    return DOMAIN_REGEX.match(candidate) is not None


def normalize_line(line: str) -> str | None:
    stripped = line.strip()

    if not stripped or stripped.startswith("#"):
        return None

    if "#" in stripped:
        stripped = stripped.split("#", 1)[0].strip()

    if not stripped:
        return None

    cleaned = stripped.lower().rstrip(".")
    if not is_valid_domain(cleaned):
        return None

    return cleaned


def extract_domains_from_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> set[str]:
    extracted = archive.extractfile(member)
    if extracted is None:
        return set()

    valid_domains: set[str] = set()
    for raw_line in extracted:
        line = raw_line.decode("utf-8", errors="ignore")
        normalized = normalize_line(line)
        if normalized:
            valid_domains.add(normalized)

    return valid_domains


def find_domains_member(
    archive: tarfile.TarFile,
    category: str,
) -> tarfile.TarInfo | None:
    for member in archive.getmembers():
        if not member.isfile():
            continue

        parts = PurePosixPath(member.name).parts
        if len(parts) >= 2 and parts[-1] == "domains" and parts[-2] == category:
            return member

    return None


def list_available_categories(archive: tarfile.TarFile) -> list[str]:
    categories = set()
    for member in archive.getmembers():
        if not member.isfile():
            continue

        parts = PurePosixPath(member.name).parts
        if len(parts) >= 2 and parts[-1] == "domains":
            categories.add(parts[-2])

    return sorted(categories)


def parse_list_groups_from_config(config: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    bundles_cfg = config.get("bundles")
    if not isinstance(bundles_cfg, list):
        return []

    bundles: list[tuple[str, list[str]]] = []
    for entry in bundles_cfg:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        categories = entry.get("categories")
        if not isinstance(name, str) or not name.strip():
            continue
        if isinstance(categories, str):
            categories_list = [part.strip() for part in categories.split(",") if part.strip()]
        elif isinstance(categories, list):
            categories_list = [str(part).strip() for part in categories if str(part).strip()]
        else:
            categories_list = []

        if not categories_list:
            continue

        bundles.append((name.strip(), categories_list))

    return bundles


def category_filename(category: str) -> str:
    return f"{FILE_PREFIX}{category}.txt"


def bundle_filename(group_id: str) -> str:
    return f"{FILE_PREFIX}{group_id}.txt"


def build_header(category: str, source_url: str, generated_at: str) -> list[str]:
    return [
        f"# Name: UT1 - {category}",
        f"# Source: {source_url}",
        f"# Generated: {generated_at}",
        "",
    ]


def write_category_file(
    category: str,
    domains: Iterable[str],
    generated_at: str,
    output_dir: Path,
) -> tuple[Path, int]:
    output_path = output_dir / category_filename(category)
    deduped_domains = collapse_subdomains(domains)

    lines = build_header(category, SOURCE_URL, generated_at)
    lines.extend(deduped_domains)

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path, len(deduped_domains)


def slugify_identifier(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "list"


def build_raw_url(filename: str) -> str:
    github_owner = os.getenv("GITHUB_OWNER", "your-github-username")
    github_repo = os.getenv("GITHUB_REPO", "your-repo-name")
    github_branch = os.getenv("GITHUB_BRANCH", "main")
    return (
        f"https://raw.githubusercontent.com/{github_owner}/"
        f"{github_repo}/{github_branch}/dist/{filename}"
    )


def build_homepage_url() -> str:
    github_owner = os.getenv("GITHUB_OWNER", "your-github-username")
    github_repo = os.getenv("GITHUB_REPO", "your-repo-name")
    return f"https://github.com/{github_owner}/{github_repo}"


def format_category_name(category: str) -> str:
    return category.replace("_", " ").replace("-", " ").strip().title()


def category_description(category: str) -> str:
    return f"UT1 category: {format_category_name(category)}"


def iter_domain_lines(handle: io.TextIOBase) -> Iterable[str]:
    for line in handle:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        yield stripped


def domain_sort_key(domain: str) -> tuple[int, str]:
    return (domain.count("."), domain)


def collapse_subdomains(domains: Iterable[str]) -> list[str]:
    unique_domains = set(domains)
    ordered = sorted(unique_domains, key=lambda domain: (domain.count("."), domain))

    kept: set[str] = set()
    for domain in ordered:
        labels = domain.split(".")
        skip = False
        for i in range(1, len(labels)):
            parent = ".".join(labels[i:])
            if parent in kept:
                skip = True
                break
        if not skip:
            kept.add(domain)

    return sorted(kept)


def write_group_files(
    group_name: str,
    categories: list[str],
    generated_at: str,
    category_source_dir: Path,
) -> tuple[list[Path], int]:
    group_id = slugify_identifier(group_name)
    base_filename = bundle_filename(group_id)

    category_paths = [category_source_dir / category_filename(category) for category in categories]
    existing_paths = [path for path in category_paths if path.exists()]

    header_lines = [
        f"# Name: UT1 Bundle - {group_name}",
        f"# Source: {SOURCE_URL}",
        f"# Generated: {generated_at}",
        f"# Categories: {', '.join(categories)}",
        "",
    ]
    header_text = "\n".join(header_lines) + "\n"
    header_bytes = len(header_text.encode("utf-8"))

    if not existing_paths:
        output_path = DIST_DIR / base_filename
        output_path.write_text(header_text, encoding="utf-8")
        return [output_path], 0

    handles = [path.open("r", encoding="utf-8") for path in existing_paths]
    files: list[Path] = []
    emitted: set[str] = set()

    def start_new_chunk(index: int) -> tuple[io.TextIOWrapper, Path, int, int]:
        if index == 1:
            filename = base_filename
        else:
            filename = base_filename.replace(".txt", f"-part{index}.txt")
        path = DIST_DIR / filename
        handle = path.open("w", encoding="utf-8")
        handle.write(header_text)
        files.append(path)
        return handle, path, header_bytes, 0

    try:
        iterators = [
            ((domain_sort_key(domain), domain) for domain in iter_domain_lines(handle))
            for handle in handles
        ]
        merged = heapq.merge(*iterators)

        chunk_index = 0
        current_file: io.TextIOWrapper | None = None
        current_bytes = 0
        current_entries = 0

        for _, domain in merged:
            if domain in emitted:
                continue

            labels = domain.split(".")
            skip = False
            for i in range(1, len(labels)):
                parent = ".".join(labels[i:])
                if parent in emitted:
                    skip = True
                    break
            if skip:
                continue

            line = f"{domain}\n"
            line_bytes = len(line.encode("utf-8"))

            if current_file is None:
                chunk_index += 1
                current_file, _, current_bytes, current_entries = start_new_chunk(chunk_index)
            elif current_entries > 0 and current_bytes + line_bytes > MAX_BUNDLE_BYTES:
                current_file.close()
                chunk_index += 1
                current_file, _, current_bytes, current_entries = start_new_chunk(chunk_index)

            current_file.write(line)
            current_bytes += line_bytes
            current_entries += 1
            emitted.add(domain)

        if current_file is not None:
            current_file.close()
    finally:
        for handle in handles:
            handle.close()

    total_entries = len(emitted)

    if len(files) > 1:
        base_root = base_filename.rsplit(".txt", 1)[0]
        renamed_files: list[Path] = []
        for index, path in enumerate(files, start=1):
            new_path = path.with_name(f"{base_root}-{index}.txt")
            path.rename(new_path)
            renamed_files.append(new_path)
        files = renamed_files

    return files, total_entries


def generate_metadata(metadata: dict[str, dict[str, str | int]], generated_at: str) -> None:
    categories_manifest = []
    for category_id in sorted(metadata):
        category_data = metadata[category_id]
        categories_manifest.append(
            {
                "id": category_id,
                "name": str(category_data["name"]),
                "description": str(category_data["description"]),
                "raw_urls": list(category_data["raw_urls"]),
                "entries_count": int(category_data["entries_count"]),
            }
        )

    manifest = {
        "source": SOURCE_URL,
        "homepage": build_homepage_url(),
        "generated_at": generated_at,
        "categories": categories_manifest,
    }
    METADATA_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_nextdns_metadata_files(metadata: dict[str, dict[str, str | int]]) -> None:
    homepage = build_homepage_url()

    for category_id in sorted(metadata):
        category_data = metadata[category_id]
        raw_urls = list(category_data["raw_urls"])

        bundle_name = f"UT1 bundle {category_id}"

        if len(raw_urls) == 1:
            targets = [(Path(f"ut1{category_id}.json"), raw_urls[0])]
        else:
            targets = [
                (Path(f"ut1{category_id}{index}.json"), raw_url)
                for index, raw_url in enumerate(raw_urls, start=1)
            ]

        target_names = [path.name for path, _ in targets]

        for file_path, raw_url in targets:
            other_targets = [name for name in target_names if name != file_path.name]
            description = str(category_data["description"])
            if other_targets:
                description += " — Ajouter aussi " + ", ".join(other_targets)
            payload = {
                "name": bundle_name,
                "website": homepage,
                "description": description,
                "source": {
                    "url": raw_url,
                    "format": "domains",
                },
            }
            file_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )


def process_all_categories(output_dir: Path) -> dict[str, dict[str, str | int]]:
    metadata: dict[str, dict[str, str | int]] = {}

    with tarfile.open(ARCHIVE_PATH, mode="r:gz") as archive:
        categories = list_available_categories(archive)
        log(f"Found {len(categories)} categories in archive.")
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        for category in categories:
            member = find_domains_member(archive, category)
            if member is None:
                log(f"domains file not found for category: {category}")
                continue

            domains = extract_domains_from_member(archive, member)
            _, entries = write_category_file(category, domains, generated_at, output_dir)

            metadata[category] = {
                "name": format_category_name(category),
                "description": category_description(category),
                "raw_urls": [build_raw_url(category_filename(category))],
                "entries_count": entries,
            }

    return metadata


def build_group_metadata(
    all_metadata: dict[str, dict[str, str | int]],
    groups: list[tuple[str, list[str]]],
    category_source_dir: Path,
) -> dict[str, dict[str, str | int]]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    grouped_metadata: dict[str, dict[str, str | int]] = {}

    for group_name, categories in groups:
        valid_categories = [category for category in categories if category in all_metadata]
        if not valid_categories:
            log(f"No valid categories found for group: {group_name}")
            continue

        missing_categories = sorted(set(categories) - set(valid_categories))
        if missing_categories:
            log(
                f"Missing categories for group {group_name}: "
                + ", ".join(missing_categories)
            )

        output_paths, entries_count = write_group_files(
            group_name,
            valid_categories,
            generated_at,
            category_source_dir,
        )
        group_id = slugify_identifier(group_name)
        description = "Combined UT1 categories: " + ", ".join(valid_categories)
        if len(output_paths) > 1:
            json_parts = [
                (f"ut1{group_id}.json" if index == 1 else f"ut1{group_id}{index}.json")
                for index in range(1, len(output_paths) + 1)
            ]
            description += (
                f" — Bundle split into {len(output_paths)} parts: "
                + ", ".join(json_parts)
                + ". Ajoutez-les toutes."
            )
        grouped_metadata[group_id] = {
            "name": group_name,
            "description": description,
            "raw_urls": [build_raw_url(path.name) for path in output_paths],
            "entries_count": entries_count,
        }

    return grouped_metadata


def main() -> None:
    start_time = time.perf_counter()
    log("Starting UT1 → NextDNS generation...")
    ensure_directories()
    config = load_config(CONFIG_PATH)
    apply_env_overrides_from_config(config)
    cleanup_previous_outputs()

    try:
        log("Downloading UT1 archive...")
        download_archive(SOURCE_URL, ARCHIVE_PATH)
        list_groups = parse_list_groups_from_config(config)

        if not list_groups:
            raise RuntimeError(
                "No bundles defined in .env. Please add at least one entry under the 'bundles' section "
                "using the YAML format described in README.md."
            )

        log(f"Processing {len(list_groups)} grouped list(s)...")
        all_metadata = process_all_categories(CATEGORY_WORK_DIR)
        metadata = build_group_metadata(all_metadata, list_groups, CATEGORY_WORK_DIR)

        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        log("Writing metadata files...")
        generate_metadata(metadata, generated_at)
        write_nextdns_metadata_files(metadata)
        log(
            "Done. "
            f"{len(all_metadata)} category file(s) processed, "
            f"{len(metadata)} published item(s) in metadata.json."
        )
    finally:
        cleanup_temporary_files()
        print_performance_summary(start_time)


if __name__ == "__main__":
    main()
