"""模拟市场与平台write_file，验证单文件分批导出；不请求真实行情。"""
import ast
import builtins
import contextlib
import inspect
import io
import types
import unittest
from unittest import mock

import pandas as pd

import csi300_to_csv as exporter
from test_csi300_history import FakeAPI


class FakeStorageAPI(FakeAPI):
    def __init__(self, missing=False, fail_append=False, short_header=False):
        super().__init__(missing=missing)
        self.files = {}
        self.write_calls = []
        self.fail_append = fail_append
        self.short_header = short_header

    def write_file(self, filename, content, append=False):
        self.write_calls.append((filename, content, append))
        if append and self.fail_append:
            raise RuntimeError("uncertain append outcome")
        if not append and self.short_header:
            return 0
        previous = self.files.get(filename, "") if append else ""
        self.files[filename] = previous + content
        return len(content.encode("utf-8"))


class StreamingExporterTests(unittest.TestCase):
    def setUp(self):
        self.original = exporter.BATCH_SIZE, exporter.REQUEST_INTERVAL
        exporter.BATCH_SIZE, exporter.REQUEST_INTERVAL = 2, 0

    def tearDown(self):
        exporter.BATCH_SIZE, exporter.REQUEST_INTERVAL = self.original

    def execute(self, api):
        with contextlib.redirect_stdout(io.StringIO()):
            summary = exporter.run_to_csv("20150105", "20150107", api=api)
        text = api.files[summary["filename"]]
        return summary, text, pd.read_csv(io.StringIO(text))

    def test_streams_batches_into_one_csv_with_one_bom_and_one_header(self):
        api = FakeStorageAPI()
        summary, text, data = self.execute(api)
        self.assertEqual(len(api.files), 1)
        self.assertEqual([call[2] for call in api.write_calls], [False, True, True])
        self.assertEqual(text.count("\ufeff"), 1)
        self.assertEqual(text.count("date,symbol,close_pre,is_paused,missing_close"), 1)
        self.assertEqual(summary["written_rows"], summary["expected_rows"])
        self.assertEqual(len(data), 6)
        self.assertFalse(data.duplicated(["date", "symbol"]).any())
        expected = {(d.strftime("%Y-%m-%d"), s)
                    for d, codes in zip(api.days, api.members) for s in codes}
        self.assertEqual(set(zip(data["date"], data["symbol"])), expected)
        for _, params in api.stock_calls:
            self.assertEqual((params["start_date"], params["end_date"], params["fq"]),
                             ("20150105", "20150107", "pre"))
        self.assertIsInstance(api.stock_calls[-1][0], str)
        paused = data[(data["date"] == "2015-01-06") & (data["symbol"] == api.b)].iloc[0]
        self.assertEqual(paused["is_paused"], 1)
        self.assertEqual(paused["close_pre"], 21)

    def test_membership_intervals_handle_removal_and_reentry(self):
        api = FakeStorageAPI()
        with contextlib.redirect_stdout(io.StringIO()):
            ranges, expected_rows, issues = exporter.get_member_ranges(api, api.days)
        self.assertEqual(ranges[api.a], [(0, 0), (2, 2)])
        self.assertEqual(ranges[api.b], [(0, 1)])
        self.assertEqual(ranges[api.c], [(1, 2)])
        self.assertEqual(expected_rows, 6)
        self.assertEqual(len(issues), 3)

    def test_missing_member_quote_stays_in_csv_and_is_counted(self):
        api = FakeStorageAPI(missing=True)
        summary, _, data = self.execute(api)
        self.assertEqual(summary["written_rows"], 6)
        self.assertEqual(summary["missing_quote_rows"], 1)
        row = data[(data["date"] == "2015-01-06") & (data["symbol"] == api.c)].iloc[0]
        self.assertTrue(pd.isnull(row["close_pre"]))
        self.assertTrue(row["missing_close"])

    def test_append_failure_stops_without_retry_or_duplicate(self):
        api = FakeStorageAPI(fail_append=True)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            exporter.run_to_csv("20150105", "20150107", api=api)
        self.assertEqual(len(api.write_calls), 2)
        self.assertEqual([call[2] for call in api.write_calls], [False, True])
        self.assertEqual(len(api.stock_calls), 1)

    def test_short_header_write_stops_before_any_stock_query(self):
        api = FakeStorageAPI(short_header=True)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            exporter.run_to_csv("20150105", "20150107", api=api)
        self.assertEqual(len(api.write_calls), 1)
        self.assertFalse(api.stock_calls)

    def test_runs_with_native_file_operations_forbidden(self):
        source = inspect.getsource(exporter)
        ast.parse(source, feature_version=(3, 5))
        code = compile(source, "<platform_csv_exporter>", "exec")
        module = types.ModuleType("platform_csv_exporter")
        original_import = builtins.__import__

        def restricted_import(name, *args, **kwargs):
            if name in {"pathlib", "os", "sys"}:
                raise ImportError("platform blocks system modules")
            return original_import(name, *args, **kwargs)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("builtins.__import__", side_effect=restricted_import))
            for target in ["builtins.open", "io.open", "os.open", "os.mkdir", "os.makedirs"]:
                stack.enter_context(mock.patch(target, side_effect=AssertionError("native file access")))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            exec(code, module.__dict__)
            module.BATCH_SIZE, module.REQUEST_INTERVAL = 2, 0
            api = FakeStorageAPI()
            summary = module.run_to_csv("20150105", "20150107", api=api)
        self.assertEqual(summary["written_rows"], 6)
        self.assertEqual(len(api.files), 1)


if __name__ == "__main__":
    unittest.main()
