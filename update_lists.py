#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import heapq
import json
import os
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

import requests

SOURCE_URL = "https://dsi.ut-capitole.fr/blacklists/download/blacklists.tar.gz"
DIST_DIR = Path("dist")
TMP_DIR = Path("tmp")
ARCHIVE_PATH = TMP_DIR / "blacklists.tar.gz"
METADATA_PATH = Path("metadata.json")
ENV_PATH = Path(".env")
PUSH_CATEGORIES_ENV = "CATEGORIES_TO_PUSH"
LIST_GROUPS_ENV = "LIST_GROUPS"

DOMAIN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


def ensure_directories() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_previous_outputs() -> None:
    for output_file in DIST_DIR.glob("toulouse-*.txt"):
        output_file.unlink(missing_ok=True)
    for metadata_file in Path(".").glob("ut1*.json"):
        metadata_file.unlink(missing_ok=True)


def cleanup_temporary_files() -> None:
    ARCHIVE_PATH.unlink(missing_ok=True)
    try:
        TMP_DIR.rmdir()
    except OSError:
        pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_push_categories() -> set[str] | None:
    raw_value = os.getenv(PUSH_CATEGORIES_ENV, "").strip()
    if not raw_value:
        return None

    categories = {
        category.strip()
        for category in raw_value.replace(";", ",").split(",")
        if category.strip()
    }
    return categories or None


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


def parse_group_definition_line(line: str) -> tuple[str, list[str]] | None:
    if ":" not in line:
        return None

    raw_name, raw_categories = line.split(":", 1)
    group_name = raw_name.strip()
    if not group_name:
        return None

    categories = [
        category.strip()
        for category in raw_categories.replace(";", ",").split(",")
        if category.strip()
    ]
    if not categories:
        return None

    return group_name, categories


def parse_list_groups() -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {}

    raw_groups_env = os.getenv(LIST_GROUPS_ENV, "").strip()
    if raw_groups_env:
        for raw_line in raw_groups_env.split("|"):
            parsed = parse_group_definition_line(raw_line.strip())
            if parsed is None:
                continue
            group_name, categories = parsed
            groups[group_name] = categories

    if ENV_PATH.exists():
        for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" in line:
                continue

            parsed = parse_group_definition_line(line)
            if parsed is None:
                continue

            group_name, categories = parsed
            groups[group_name] = categories

    return list(groups.items())


def build_header(category: str, source_url: str, generated_at: str) -> list[str]:
    return [
        f"# Name: Toulouse UT1 - {category}",
        f"# Source: {source_url}",
        f"# Generated: {generated_at}",
        "",
    ]


def write_category_file(category: str, domains: Iterable[str], generated_at: str) -> Path:
    output_path = DIST_DIR / f"toulouse-{category}.txt"
    sorted_domains = sorted(set(domains))

    lines = build_header(category, SOURCE_URL, generated_at)
    lines.extend(sorted_domains)

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


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
    homepage = os.getenv("HOMEPAGE", "").strip()
    if homepage:
        return homepage

    github_owner = os.getenv("GITHUB_OWNER", "your-github-username")
    github_repo = os.getenv("GITHUB_REPO", "your-repo-name")
    return f"https://github.com/{github_owner}/{github_repo}"


def format_category_name(category: str) -> str:
    return category.replace("_", " ").replace("-", " ").strip().title()


def category_description(category: str) -> str:
    return f"UT1 category: {format_category_name(category)}"


def iter_domain_lines(file_handle) -> Iterable[str]:
    for raw_line in file_handle:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        yield line


def write_group_file(group_name: str, categories: list[str], generated_at: str) -> tuple[Path, int]:
    group_id = slugify_identifier(group_name)
    output_filename = f"toulouse-bundle-{group_id}.txt"
    output_path = DIST_DIR / output_filename

    category_paths = [DIST_DIR / f"toulouse-{category}.txt" for category in categories]
    existing_paths = [path for path in category_paths if path.exists()]

    if not existing_paths:
        header = [
            f"# Name: Toulouse UT1 Bundle - {group_name}",
            f"# Source: {SOURCE_URL}",
            f"# Generated: {generated_at}",
            f"# Categories: {', '.join(categories)}",
            "",
        ]
        output_path.write_text("\n".join(header) + "\n", encoding="utf-8")
        return output_path, 0

    handles = [path.open("r", encoding="utf-8") for path in existing_paths]
    try:
        iterators = [iter_domain_lines(handle) for handle in handles]
        merged = heapq.merge(*iterators)

        lines = [
            f"# Name: Toulouse UT1 Bundle - {group_name}",
            f"# Source: {SOURCE_URL}",
            f"# Generated: {generated_at}",
            f"# Categories: {', '.join(categories)}",
            "",
        ]

        count = 0
        previous = None
        with output_path.open("w", encoding="utf-8") as output_file:
            output_file.write("\n".join(lines) + "\n")
            for domain in merged:
                if domain == previous:
                    continue
                output_file.write(f"{domain}\n")
                previous = domain
                count += 1
    finally:
        for handle in handles:
            handle.close()

    return output_path, count


def generate_metadata(metadata: dict[str, dict[str, str | int]], generated_at: str) -> None:
    categories_manifest = []
    for category_id in sorted(metadata):
        category_data = metadata[category_id]
        categories_manifest.append(
            {
                "id": category_id,
                "name": str(category_data["name"]),
                "description": str(category_data["description"]),
                "raw_url": str(category_data["raw_url"]),
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
        file_path = Path(f"ut1{category_id}.json")
        payload = {
            "name": str(category_data["name"]),
            "website": homepage,
            "description": str(category_data["description"]),
            "source": {
                "url": str(category_data["raw_url"]),
                "format": "domains",
            },
        }
        file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def process_all_categories() -> dict[str, dict[str, str | int]]:
    metadata: dict[str, dict[str, str | int]] = {}

    with tarfile.open(ARCHIVE_PATH, mode="r:gz") as archive:
        categories = list_available_categories(archive)
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        for category in categories:
            member = find_domains_member(archive, category)
            if member is None:
                print(f"domains file not found for category: {category}")
                continue

            domains = extract_domains_from_member(archive, member)
            write_category_file(category, domains, generated_at)

            metadata[category] = {
                "name": format_category_name(category),
                "description": category_description(category),
                "raw_url": build_raw_url(f"toulouse-{category}.txt"),
                "entries_count": len(domains),
            }

    return metadata


def filter_metadata_for_push(
    all_metadata: dict[str, dict[str, str | int]],
    categories_to_push: set[str] | None,
) -> dict[str, dict[str, str | int]]:
    if categories_to_push is None:
        return all_metadata

    filtered: dict[str, dict[str, str | int]] = {}
    for category in sorted(categories_to_push):
        details = all_metadata.get(category)
        if details is None:
            print(f"Requested category in .env not found: {category}")
            continue
        filtered[category] = details

    return filtered


def build_group_metadata(
    all_metadata: dict[str, dict[str, str | int]],
    groups: list[tuple[str, list[str]]],
) -> dict[str, dict[str, str | int]]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    grouped_metadata: dict[str, dict[str, str | int]] = {}

    for group_name, categories in groups:
        valid_categories = [category for category in categories if category in all_metadata]
        if not valid_categories:
            print(f"No valid categories found for group: {group_name}")
            continue

        missing_categories = sorted(set(categories) - set(valid_categories))
        if missing_categories:
            print(
                f"Missing categories for group {group_name}: "
                + ", ".join(missing_categories)
            )

        output_path, entries_count = write_group_file(group_name, valid_categories, generated_at)
        group_id = slugify_identifier(group_name)
        grouped_metadata[group_id] = {
            "name": group_name,
            "description": "Combined UT1 categories: " + ", ".join(valid_categories),
            "raw_url": build_raw_url(output_path.name),
            "entries_count": entries_count,
        }

    return grouped_metadata


def main() -> None:
    ensure_directories()
    load_dotenv(ENV_PATH)
    cleanup_previous_outputs()

    try:
        download_archive(SOURCE_URL, ARCHIVE_PATH)

        all_metadata = process_all_categories()
        list_groups = parse_list_groups()

        if list_groups:
            metadata = build_group_metadata(all_metadata, list_groups)
        else:
            categories_to_push = parse_push_categories()
            metadata = filter_metadata_for_push(all_metadata, categories_to_push)

        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        generate_metadata(metadata, generated_at)
        write_nextdns_metadata_files(metadata)
        print(
            "Done. "
            f"{len(all_metadata)} dist file(s) generated, "
            f"{len(metadata)} published item(s) in metadata.json."
        )
    finally:
        cleanup_temporary_files()


if __name__ == "__main__":
    main()
