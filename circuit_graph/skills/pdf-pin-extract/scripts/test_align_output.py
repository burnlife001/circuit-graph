import importlib.util
import unittest
from pathlib import Path

import pandas as pd


def load_module(name):
    module_path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


align_output = load_module("align_output")
extract_tables = load_module("extract_tables")


def build_pin(name, main_function=""):
    return {
        "name": name,
        "type": "",
        "io_level": "",
        "main_function": main_function,
        "multiplex_functions": [],
        "additional_functions": [],
        "packages": {"LQFP48": 1},
        "alternate_functions": {"AF0": None},
    }


class BuildJsonMainFunctionTest(unittest.TestCase):
    def build_main_function(self, name):
        pin_df = pd.DataFrame([{
            "LQFP48": "1",
            "Name": name,
            "Type (1)": "",
            "I/O level (2)": "",
            "Main function": "",
            "Multiplex function": "",
            "Additional function": "",
        }])
        data = extract_tables.build_json(pin_df, {}, "TEST", "source.pdf", 0)
        return data["pins"][0]["main_function"]

    def test_nc_main_function_stays_empty(self):
        self.assertEqual("", self.build_main_function("NC"))

    def test_gpio_main_function_falls_back_to_pin_name(self):
        self.assertEqual("PA0", self.build_main_function("PA0/WKUP"))


class BuildDocMainFunctionTest(unittest.TestCase):
    def build_doc(self, pin):
        raw = {
            "chip_name": "TEST",
            "source_pdf": "source.pdf",
            "packages": ["LQFP48"],
            "pins": [pin],
        }
        return align_output.build_doc(raw, {}, ["PA"], {})

    def build_main(self, pin):
        return self.build_doc(pin)["pinout"][0]["main"]

    def test_nc_with_empty_main_function_stays_empty(self):
        self.assertIsNone(self.build_main(build_pin("NC")))

    def test_gpio_canonical_main_function_is_preserved(self):
        self.assertEqual("PA0", self.build_main(build_pin("PA0/WKUP", "PA0")))

    def test_rendered_nc_row_uses_blank_cells(self):
        markdown = align_output.render_md(self.build_doc(build_pin("NC")))
        pin_line = next(line for line in markdown.splitlines() if line.startswith("|1|"))
        self.assertEqual("|1|NC| | | | | |", pin_line)


if __name__ == "__main__":
    unittest.main()
