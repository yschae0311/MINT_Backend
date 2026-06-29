#!/usr/bin/env python3
"""Extract ca_certs.zip from a colleague and wire ELASTICSEARCH_CA_CERTS in .env."""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

from app.search.es_client import _CA_FILE_CANDIDATES, find_ca_in_directory

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CERT_DIR = BACKEND_ROOT / "certs" / "elasticsearch"
DEFAULT_CA_REL = Path("certs/elasticsearch/http_ca.crt")


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)


def pick_ca_file(dest: Path) -> Path:
    found = find_ca_in_directory(dest)
    if not found:
        names = ", ".join(_CA_FILE_CANDIDATES)
        raise SystemExit(
            f"No CA file found under {dest}. Expected one of: {names} or any *.crt/*.pem"
        )
    return found


def ensure_http_ca_link(source: Path, dest: Path) -> Path:
    target = dest / "http_ca.crt"
    if source.resolve() == target.resolve():
        return target
    shutil.copy2(source, target)
    return target


def update_env(ca_rel: Path) -> None:
    env_path = BACKEND_ROOT / ".env"
    if not env_path.is_file():
        print(f"Skip .env update (missing {env_path})")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    key = "ELASTICSEARCH_CA_CERTS="
    value = f"{key}{ca_rel.as_posix()}"
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(key):
            lines[index] = value
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Elasticsearch TLS (ca_certs.zip)")
        lines.append(value)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {env_path} → {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Elasticsearch CA from ca_certs.zip")
    parser.add_argument("zip_path", type=Path, help="Path to ca_certs.zip from your colleague")
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="Do not modify .env (only extract certs)",
    )
    args = parser.parse_args()

    zip_path = args.zip_path.expanduser().resolve()
    if not zip_path.is_file():
        raise SystemExit(f"Zip not found: {zip_path}")

    extract_zip(zip_path, CERT_DIR)
    ca_source = pick_ca_file(CERT_DIR)
    ca_target = ensure_http_ca_link(ca_source, CERT_DIR)
    rel = ca_target.relative_to(BACKEND_ROOT)

    print(f"CA installed: {ca_target}")
    print(f"Set in .env: ELASTICSEARCH_CA_CERTS={rel.as_posix()}")

    if not args.no_env:
        update_env(rel)

    print("Then set ELASTICSEARCH_URL=https://... and credentials, run: python3 scripts/es_ping.py")


if __name__ == "__main__":
    main()
