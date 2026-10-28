"""本地核验并排序已下载CSI300 CSV；全部字段按字符串读写，保留价格原文。"""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def audit_and_sort(source):
    source = Path(source).expanduser().resolve()
    sorted_path = source.with_name(source.stem + "_按日期排序.csv")
    count_path = source.with_name(source.stem + "_每日股票数量.csv")
    for output in (sorted_path, count_path):
        if output.exists():
            raise FileExistsError("不覆盖已存在的文件：{}".format(output))

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    data = pd.read_csv(source, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    expected_columns = ["date", "symbol", "close_pre", "is_paused", "missing_close"]
    if list(data.columns) != expected_columns:
        raise ValueError("CSV列结构与导出脚本不一致：{}".format(list(data.columns)))
    pd.to_datetime(data["date"], format="%Y-%m-%d", errors="raise")
    counts = data.groupby("date").agg(row_count=("symbol", "size"), stock_count=("symbol", "nunique"))
    duplicate_rows = data.duplicated(["date", "symbol"])
    counts["duplicate_rows"] = duplicate_rows.groupby(data["date"]).sum().astype(int)
    counts["missing_close_count"] = data["close_pre"].eq("").groupby(data["date"]).sum().astype(int)
    counts = counts.sort_index()
    summary = {
        "source": str(source),
        "source_sha256": source_hash,
        "data_rows": len(data),
        "trading_dates": len(counts),
        "historical_symbols": int(data["symbol"].nunique()),
        "first_date": data["date"].min(),
        "last_date": data["date"].max(),
        "daily_stock_count_min": int(counts["stock_count"].min()),
        "daily_stock_count_max": int(counts["stock_count"].max()),
        "non_300_dates": int(counts["stock_count"].ne(300).sum()),
        "duplicate_date_symbol_rows": int(duplicate_rows.sum()),
        "empty_cells": {name: int(value) for name, value in data.eq("").sum().items()},
        "missing_close_true_rows": int(data["missing_close"].str.lower().eq("true").sum()),
        "original_globally_sorted": bool(pd.MultiIndex.from_frame(data[["date", "symbol"]]).is_monotonic_increasing),
    }
    first_batch = data.iloc[:7063]
    summary["first_7063_rows_stock_counts"] = {
        day: int(first_batch.loc[first_batch["date"] == day, "symbol"].nunique())
        for day in ["2015-01-05", "2026-10-28"]
    }
    summary["last_3_rows_symbols"] = data["symbol"].tail(3).tolist()

    # Only reorder existing rows. Do not infer numeric types, adjust, fill or drop data.
    ordered = data.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)
    ordered.to_csv(sorted_path, index=False, encoding="utf-8-sig")
    counts.to_csv(count_path, index=True, encoding="utf-8-sig")

    restored = pd.read_csv(sorted_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(restored, ordered)
    restored_counts = restored.groupby("date")["symbol"].nunique()
    pd.testing.assert_series_equal(restored_counts, counts["stock_count"], check_names=False)
    assert pd.MultiIndex.from_frame(restored[["date", "symbol"]]).is_monotonic_increasing
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    summary["sorted_file"] = str(sorted_path)
    summary["daily_count_file"] = str(count_path)
    summary["export_all_fields_exact_match"] = True
    summary["original_file_unchanged"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="下载后的CSV绝对路径")
    args = parser.parse_args()
    audit_and_sort(args.csv)
