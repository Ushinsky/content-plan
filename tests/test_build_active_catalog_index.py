import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_active_catalog_index import build_index


class BuildActiveCatalogIndexTests(unittest.TestCase):
    def test_indexes_only_active_product_rows_and_keeps_category_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "catalog.csv"
            output = root / "generated"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["Тип записи", "Активность", "Категория", "Название"],
                    delimiter=";",
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"Тип записи": "Товар", "Активность": "Да", "Категория": "Плоттеры", "Название": "Plotter A"},
                        {"Тип записи": "Товар", "Активность": "Нет", "Категория": "Плоттеры", "Название": "Plotter B"},
                        {"Тип записи": "Раздел", "Активность": "Да", "Категория": "МФУ", "Название": "MFP category"},
                        {"Тип записи": "Товар", "Активность": "Да", "Категория": "МФУ", "Название": "MFP C"},
                    ]
                )

            manifest = build_index(source, output)

            self.assertEqual(manifest["active_product_count"], 2)
            self.assertEqual({item["category"] for item in manifest["categories"]}, {"Плоттеры", "МФУ"})
            indexed_rows = []
            for item in manifest["categories"]:
                for shard in item["files"]:
                    with (output / shard["file"]).open(encoding="utf-8-sig", newline="") as stream:
                        indexed_rows.extend(csv.DictReader(stream, delimiter=";"))
            self.assertEqual({row["Название"] for row in indexed_rows}, {"Plotter A", "MFP C"})
            self.assertTrue(all(row["Тип записи"] == "Товар" and row["Активность"] == "Да" for row in indexed_rows))
            self.assertEqual(json.loads((output / "manifest.json").read_text(encoding="utf-8")), manifest)

    def test_fails_closed_if_required_status_columns_are_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "catalog.csv"
            source.write_text("Категория,Название\nПлоттеры,Plotter A\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Тип записи.*Активность"):
                build_index(source, root / "generated")

            self.assertFalse((root / "generated" / "manifest.json").exists())

    def test_fails_closed_if_no_active_products_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "catalog.csv"
            source.write_text(
                "Тип записи;Активность;Категория;Название\n"
                "Товар;Нет;Плоттеры;Inactive model\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "не найдено ни одной активной"):
                build_index(source, root / "generated")


if __name__ == "__main__":
    unittest.main()

