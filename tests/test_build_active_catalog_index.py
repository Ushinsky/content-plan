import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_active_catalog_index import build_index


class BuildActiveCatalogIndexTests(unittest.TestCase):
    def test_exports_only_active_product_rows_and_minimal_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "catalog.csv"
            output = root / "generated"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["Тип записи", "Тип товара", "Название", "Активность", "Сорт.", "ID", "SALES_NOTES (SALES_NOTES)"],
                    delimiter=";",
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"Тип записи": "Товар", "Тип товара": "простой", "Название": "Plotter A", "Активность": "Да", "Сорт.": "1", "ID": "101", "SALES_NOTES (SALES_NOTES)": "internal note"},
                        {"Тип записи": "Товар", "Тип товара": "простой", "Название": "Plotter B", "Активность": "Нет", "Сорт.": "2", "ID": "102", "SALES_NOTES (SALES_NOTES)": "internal note"},
                        {"Тип записи": "Раздел", "Тип товара": "", "Название": "MFP category", "Активность": "Да", "Сорт.": "0", "ID": "200", "SALES_NOTES (SALES_NOTES)": "internal note"},
                        {"Тип записи": "Товар", "Тип товара": "простой", "Название": "MFP C", "Активность": "Да", "Сорт.": "3", "ID": "103", "SALES_NOTES (SALES_NOTES)": "internal note"},
                    ]
                )

            manifest = build_index(source, output)

            self.assertEqual(manifest["active_product_count"], 2)
            raw_source = source.read_bytes()
            expected_blob_sha = hashlib.sha1(
                b"blob " + str(len(raw_source)).encode("ascii") + b"\0" + raw_source
            ).hexdigest()
            self.assertEqual(manifest["source_git_blob_sha"], expected_blob_sha)
            indexed_rows = []
            for shard in manifest["shards"]:
                with (output / shard["file"]).open(encoding="utf-8-sig", newline="") as stream:
                    indexed_rows.extend(csv.DictReader(stream, delimiter=";"))
            self.assertEqual({row["Название"] for row in indexed_rows}, {"Plotter A", "MFP C"})
            self.assertTrue(all(row["Тип записи"] == "Товар" and row["Активность"] == "Да" for row in indexed_rows))
            self.assertTrue(all(set(row) == {"Тип записи", "Название", "Активность", "ID"} for row in indexed_rows))
            self.assertNotIn("SALES_NOTES (SALES_NOTES)", indexed_rows[0])
            self.assertEqual(json.loads((output / "manifest.json").read_text(encoding="utf-8")), manifest)

    def test_fails_closed_if_required_status_columns_are_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "catalog.csv"
            source.write_text("Тип записи,Активность,Название\nТовар,Да,Plotter A\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "ID"):
                build_index(source, root / "generated")

            self.assertFalse((root / "generated" / "manifest.json").exists())

    def test_fails_closed_if_no_active_products_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "catalog.csv"
            source.write_text(
                "Тип записи;Активность;Название;ID\n"
                "Товар;Нет;Inactive model;1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "не найдено ни одной активной"):
                build_index(source, root / "generated")


if __name__ == "__main__":
    unittest.main()

