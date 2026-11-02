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
from collections import defaultdict
from pathlib import Path


HEADER_ALIASES = {
    "record_type": ("Тип записи",),
    "activity": ("Активность",),
    "category": ("Тип товара", "Категория", "Категория товара", "Раздел каталога", "Название раздела", "Раздел"),
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
        required = "Тип записи и Активность"
        if "category" in missing:
            required += ", а также столбец категории/раздела"
        raise ValueError(f"Не найдены обязательные поля: {required}. Заголовки CSV: {headers!r}")
    return resolved


def _read_active_products(source: Path) -> tuple[list[str], dict[str, list[dict[str, str]]], dict[str, str]]:
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
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in reader:
            if not row:
                continue
            normalized = {str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None}
            record_type = normalized.get(columns["record_type"], "").casefold()
            activity = normalized.get(columns["activity"], "").casefold()
            if record_type == "товар" and activity == "да":
                category = normalized.get(columns["category"], "").strip()
                if not category:
                    raise ValueError("В строке активного товара отсутствует значение категории/раздела")
                grouped[category].append(normalized)
    if not grouped:
        raise ValueError("В CSV не найдено ни одной активной строки с Тип записи=Товар и Активность=Да")
    return headers, grouped, columns


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
    headers, grouped, columns = _read_active_products(source)

    # This output directory is generated-only; remove its previous contents before rebuilding.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    categories = []
    for category in sorted(grouped, key=str.casefold):
        category_rows = grouped[category]
        category_key = hashlib.sha1(category.encode("utf-8")).hexdigest()[:12]
        parts: list[list[dict[str, str]]] = []
        current: list[dict[str, str]] = []
        for row in category_rows:
            proposed = current + [row]
            if current and len(_render_csv(headers, proposed, ";")) > max_shard_bytes:
                parts.append(current)
                current = [row]
            else:
                current = proposed
        if current:
            parts.append(current)

        files = []
        for part_number, rows in enumerate(parts, start=1):
            filename = f"category-{category_key}-part-{part_number:03d}.csv"
            payload = _render_csv(headers, rows, ";")
            (output_dir / filename).write_bytes(payload)
            files.append({"file": filename, "rows": len(rows), "bytes": len(payload)})
        categories.append({"category": category, "rows": len(category_rows), "files": files})

    manifest = {
        "format_version": 1,
        "source": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "filter": {"Тип записи": "Товар", "Активность": "Да"},
        "columns": columns,
        "active_product_count": sum(len(rows) for rows in grouped.values()),
        "categories": categories,
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
        f"{len(manifest['categories'])} categories."
    )


if __name__ == "__main__":
    main()

