#!/usr/bin/env python3
"""Extract colleague ca_certs.zip into certs/elasticsearch/."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from app.search.es_client import _CA_FILE_CANDIDATES, find_ca_in_directory

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DIR = _BACKEND_ROOT / "certs" / "elasticsearch"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Elasticsearch CA from ca_certs.zip")
    parser.add_argument("zip_path", type=Path, help="Path to ca_certs.zip from colleague")
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_DIR,
        help=f"Output directory (default: {_DEFAULT_DIR})",
    )
    args = parser.parse_args()

    if not args.zip_path.is_file():
        raise SystemExit(f"Zip not found: {args.zip_path}")

    args.out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip_path) as zf:
        zf.extractall(args.out)

    ca = find_ca_in_directory(args.out)
    if ca:
        print(f"OK: extracted CA → {ca}")
        print(f"Set in .env: ELASTICSEARCH_CA_CERTS={ca.relative_to(_BACKEND_ROOT)}")
    else:
        print(f"Extracted to {args.out}, but no .crt/.pem found.")
        print(f"Expected one of: {', '.join(_CA_FILE_CANDIDATES)}")
        for item in sorted(args.out.iterdir()):
            print(f"  - {item.name}")


if __name__ == "__main__":
    main()
