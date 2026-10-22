"""离线测试：使用模拟数据验证数据口径，不访问实际行情接口。"""
import contextlib
import ast
import builtins
import io
import inspect
import types
import unittest
from unittest import mock

import pandas as pd

import csi300_history as downloader


class FakeAPI:
    def __init__(self, missing=False):
        self.days = pd.to_datetime(["2015-01-05", "2015-01-06", "2015-01-07"])
        self.a, self.b, self.c = "000001.SZ", "600000.SH", "600519.SH"
        self.members = [[self.a, self.b], [self.b, self.c], [self.a, self.c]]
        self.member_calls = 0
        self.stock_calls = []
        self.missing = missing

    def get_trade_days(self, start_date, end_date):
        return self.days[(self.days >= pd.Timestamp(start_date)) & (self.days <= pd.Timestamp(end_date))]

    def get_index_stocks(self, symbol, date):
        self.member_calls += 1
        return self.members[list(self.days).index(pd.Timestamp(date))]

    def get_price(self, securities, **kwargs):
        days = self.get_trade_days(kwargs["start_date"], kwargs["end_date"])
        if securities == downloader.INDEX_CODE:
            return pd.DataFrame({"close": [3000.0] * len(days)}, index=days)
        self.stock_calls.append((securities, kwargs.copy()))
        self_test = self

        def frame(symbol):
            base = {self_test.a: 10.0, self_test.b: 20.0, self_test.c: 30.0}[symbol]
            result = pd.DataFrame({"close": [base + i for i in range(len(days))],
                                   "is_paused": [0.0] * len(days)}, index=days)
            if symbol == self_test.b and pd.Timestamp("20150106") in days:
                result.loc[pd.Timestamp("20150106"), "is_paused"] = 1
            if self_test.missing and symbol == self_test.c and pd.Timestamp("20150106") in days:
                result = result.drop(pd.Timestamp("20150106"))
            return result

        if isinstance(securities, str):
            return frame(securities)
        return {symbol: frame(symbol) for symbol in securities}


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.original = downloader.BATCH_SIZE, downloader.REQUEST_INTERVAL
        downloader.BATCH_SIZE, downloader.REQUEST_INTERVAL = 2, 0
        self.last_stdout = ""

    def tearDown(self):
        downloader.BATCH_SIZE, downloader.REQUEST_INTERVAL = self.original

    def execute(self, api, end="20150107"):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = downloader.run("20150105", end, api=api)
        self.last_stdout = output.getvalue()
        return result

    def test_membership_changes_single_stock_return_and_same_query(self):
        api = FakeAPI()
        long, wide, members, summary = self.execute(api)
        self.assertEqual(len(long), 6)
        self.assertEqual(summary["historical_symbols"], 3)
        self.assertEqual(summary["non_300_days"], 3)
        self.assertFalse(summary["data_checks_passed"])
        self.assertEqual(summary["rows"], 6)
        self.assertEqual(int(members.to_numpy().sum()), 6)
        self.assertTrue(pd.isna(wide.loc["2015-01-05", api.c]))
        self.assertEqual(members.loc["2015-01-06", api.a], 0)
        paused = long[(long["date"] == "2015-01-06") & (long["symbol"] == api.b)].iloc[0]
        self.assertEqual(paused["close_pre"], 21.0)
        self.assertEqual(paused["is_paused"], 1)
        self.assertIsInstance(api.stock_calls[-1][0], str)
        for _, params in api.stock_calls:
            self.assertEqual((params["start_date"], params["end_date"], params["fq"]),
                             ("20150105", "20150107", "pre"))
            self.assertFalse(params["skip_paused"])
            self.assertEqual(params["fields"], ["close", "is_paused"])
        self.assertTrue(self.last_stdout)

    def test_missing_member_day_is_preserved_and_reported(self):
        api = FakeAPI(missing=True)
        long, wide, members, summary = self.execute(api)
        self.assertEqual(len(long), 6)
        self.assertEqual(summary["missing_quote_rows"], 1)
        self.assertEqual(members.loc["2015-01-06", api.c], 1)
        self.assertTrue(pd.isna(wide.loc["2015-01-06", api.c]))
        report = long[long["missing_close"]]
        self.assertEqual(len(report), 1)
        self.assertEqual(report.iloc[0]["symbol"], api.c)
        self.assertEqual(report.iloc[0]["date"], "2015-01-06")
        self.assertTrue(pd.isna(report.iloc[0]["is_paused"]))
        self.assertFalse(summary["data_checks_passed"])

    def test_changed_end_date_requests_the_requested_full_interval(self):
        api = FakeAPI()
        first = self.execute(api)[0]
        original_calls = len(api.stock_calls)
        second, _, _, summary = self.execute(api, end="20150106")
        self.assertEqual(len(first), 6)
        self.assertEqual(len(second), 4)
        self.assertEqual(api.member_calls, 5)
        self.assertGreater(len(api.stock_calls), original_calls)
        self.assertEqual(pd.Timestamp(summary["actual_end"]), pd.Timestamp("2015-01-06"))
        for _, params in api.stock_calls[:original_calls]:
            self.assertEqual(params["end_date"], "20150107")
        for _, params in api.stock_calls[original_calls:]:
            self.assertEqual((params["start_date"], params["end_date"], params["fq"]),
                             ("20150105", "20150106", "pre"))

    def test_import_and_run_work_when_file_access_and_pathlib_are_forbidden(self):
        # Read test input before enforcing the simulated cloud restrictions.
        # pandas has already been imported above, as it is in the cloud notebook.
        source = inspect.getsource(downloader)
        code = compile(source, "<csi300_history_cloud>", "exec")
        module = types.ModuleType("csi300_history_cloud")
        original_import = builtins.__import__

        def restricted_import(name, *args, **kwargs):
            if name == "pathlib" or name.startswith("pathlib."):
                raise ImportError("cloud notebook forbids pathlib")
            return original_import(name, *args, **kwargs)

        forbidden = AssertionError("the downloader must not access files")
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("builtins.__import__", side_effect=restricted_import))
            for target in ("builtins.open", "io.open", "os.open", "os.mkdir", "os.makedirs",
                           "pandas.read_csv", "pandas.DataFrame.to_csv"):
                stack.enter_context(mock.patch(target, side_effect=forbidden))
            stack.enter_context(contextlib.redirect_stdout(output))
            exec(code, module.__dict__)
            module.BATCH_SIZE, module.REQUEST_INTERVAL = 2, 0
            long, wide, members, summary = module.run("20150105", "20150107", api=FakeAPI())
        self.assertEqual(len(long), 6)
        self.assertEqual(int(members.to_numpy().sum()), 6)
        self.assertEqual(wide.shape, (3, 3))
        self.assertEqual(summary["missing_quote_rows"], 0)
        self.assertTrue(output.getvalue())

    def test_duplicate_members_rejected(self):
        with self.assertRaises(ValueError):
            downloader.check_members(["000001.SZ", "000001.SZ"], "20150105")

    def test_python35_syntax_and_legacy_pandas_merge_signature(self):
        source = inspect.getsource(downloader)
        tree = ast.parse(source, feature_version=(3, 5))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertFalse(any(isinstance(node.func, ast.Attribute)
                             and node.func.attr in {"isna", "notna", "to_numpy"}
                             for node in calls))
        original_merge = pd.DataFrame.merge

        # pandas before 0.21 has no validate argument. Enforce the old signature.
        def legacy_merge(frame, right, how="inner", on=None):
            return original_merge(frame, right, how=how, on=on)

        with mock.patch.object(pd.DataFrame, "merge", new=legacy_merge):
            long, wide, members, summary = self.execute(FakeAPI(missing=True))
        self.assertEqual(len(long), 6)
        self.assertEqual(summary["missing_quote_rows"], 1)
        self.assertEqual(members.loc["2015-01-06", "600519.SH"], 1)
        self.assertTrue(pd.isnull(wide.loc["2015-01-06", "600519.SH"]))

    def test_pasted_notebook_code_runs_full_range_without_a_test_switch(self):
        source = inspect.getsource(downloader)
        api = FakeAPI()
        now = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None)
        closed = now.normalize() - pd.Timedelta(days=int(now.hour < 15))
        api.days = pd.date_range(end=closed, periods=3, freq="B")
        namespace = {"__name__": "__main__"}
        output = io.StringIO()
        with mock.patch.dict("sys.modules", {"mindgo_api": api}), \
                mock.patch("time.sleep"), contextlib.redirect_stdout(output):
            exec(compile(source, "<pasted_notebook_code>", "exec"), namespace)
        self.assertEqual(namespace["summary"]["start"], "20150101")
        self.assertEqual(namespace["summary"]["actual_end"], api.days[-1].strftime("%Y%m%d"))
        self.assertEqual(len(namespace["close_long"]), 6)
        self.assertNotIn("TEST_ONLY", namespace)
        self.assertIn("missing_quotes", namespace)
        for _, params in api.stock_calls:
            self.assertEqual(params["start_date"], "20150101")
            self.assertEqual(params["end_date"], api.days[-1].strftime("%Y%m%d"))
            self.assertEqual(params["fq"], "pre")


if __name__ == "__main__":
    unittest.main()
