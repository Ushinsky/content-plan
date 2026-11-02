#!/usr/bin/env python3
"""Create small, verifiable shards of the active ForOffice product catalog."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
from pathlib import Path


HEADER_ALIASES = {
    "record_type": ("Тип записи",),
    "activity": ("Активность",),
    "name": ("Название", "Наименование", "Название товара", "Наименование товара"),
    "id": ("ID",),
}
MAX_SHARD_BYTES = 250_000


def _normalize_header(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", value.casefold().replace("ё", "е"))


def _resolve_headers(headers: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(header): header for header in headers}
    resolved = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            match = normalized.get(_normalize_header(alias))
            if match:
                resolved[field] = match
                break
    missing = [field for field in HEADER_ALIASES if field not in resolved]
    if missing:
        labels = {
            "record_type": "Тип записи",
            "activity": "Активность",
            "name": "Название",
            "id": "ID",
        }
        required = ", ".join(labels[field] for field in missing)
        raise ValueError(f"Не найдены обязательные поля: {required}. Заголовки CSV: {headers!r}")
    return resolved


def _read_active_products(source: Path) -> tuple[list[str], list[dict[str, str]], dict[str, str]]:
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(65_536)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"
        reader = csv.DictReader(stream, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV пустой или не содержит строки заголовков")
        headers = [header.strip() for header in reader.fieldnames]
        columns = _resolve_headers(headers)
        products = []
        for row in reader:
            if not row:
                continue
            normalized = {str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None}
            record_type = normalized.get(columns["record_type"], "").casefold()
            activity = normalized.get(columns["activity"], "").casefold()
            if record_type == "товар" and activity == "да":
                product_name = normalized.get(columns["name"], "").strip()
                product_id = normalized.get(columns["id"], "").strip()
                if not product_name or not product_id:
                    raise ValueError("В строке активного товара отсутствует название или ID")
                products.append(
                    {
                        columns["record_type"]: normalized[columns["record_type"]],
                        columns["name"]: product_name,
                        columns["activity"]: normalized[columns["activity"]],
                        columns["id"]: product_id,
                    }
                )
    if not products:
        raise ValueError("В CSV не найдено ни одной активной строки с Тип записи=Товар и Активность=Да")
    return headers, products, columns


def _render_csv(headers: list[str], rows: list[dict[str, str]], delimiter: str) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=headers, delimiter=delimiter, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode("utf-8")


def build_index(source: Path | str, output_dir: Path | str, max_shard_bytes: int = MAX_SHARD_BYTES) -> dict:
    source = Path(source)
    output_dir = Path(output_dir)
    if max_shard_bytes < 1:
        raise ValueError("max_shard_bytes must be positive")
    _, products, columns = _read_active_products(source)
    headers = [columns["record_type"], columns["name"], columns["activity"], columns["id"]]

    # This output directory is generated-only; remove its previous contents before rebuilding.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    shards: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_bytes = len(_render_csv(headers, [], ";"))
    for row in products:
        row_bytes = len(_render_csv(headers, [row], ";")) - len(_render_csv(headers, [], ";"))
        if current and current_bytes + row_bytes > max_shard_bytes:
            shards.append(current)
            current = []
            current_bytes = len(_render_csv(headers, [], ";"))
        current.append(row)
        current_bytes += row_bytes
    if current:
        shards.append(current)

    shard_manifest = []
    for part_number, rows in enumerate(shards, start=1):
        filename = f"active-products-part-{part_number:03d}.csv"
        payload = _render_csv(headers, rows, ";")
        (output_dir / filename).write_bytes(payload)
        shard_manifest.append({"file": filename, "rows": len(rows), "bytes": len(payload)})

    source_bytes = source.read_bytes()
    manifest = {
        "format_version": 1,
        "source": source.name,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_git_blob_sha": hashlib.sha1(
            b"blob " + str(len(source_bytes)).encode("ascii") + b"\0" + source_bytes
        ).hexdigest(),
        "filter": {"Тип записи": "Товар", "Активность": "Да"},
        "columns": columns,
        "exported_columns": headers,
        "active_product_count": len(products),
        "shards": shard_manifest,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-shard-bytes", type=int, default=MAX_SHARD_BYTES)
    args = parser.parse_args()
    manifest = build_index(args.source, args.output_dir, args.max_shard_bytes)
    print(
        f"Indexed {manifest['active_product_count']} active products across "
        f"{len(manifest['shards'])} shards."
    )


if __name__ == "__main__":
    main()

