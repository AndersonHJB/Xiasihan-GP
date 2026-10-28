import time
import pandas as pd

START_DATE = "20150101"
INDEX_CODE = "000300.SH"

BATCH_SIZE = 5
REQUEST_INTERVAL = 0.2
RETRIES = 3

COLUMNS = [
    "date",
    "symbol",
    "close_pre",
    "is_paused",
    "missing_close"
]


def query(func, *args, **kwargs):
    """只重试查询，不自动重试追加写入。"""
    for attempt in range(RETRIES):
        try:
            result = func(*args, **kwargs)
            time.sleep(REQUEST_INTERVAL)
            return result
        except Exception as exc:
            if attempt + 1 == RETRIES:
                raise

            print("查询重试：{}".format(exc), flush=True)
            time.sleep(2 ** (attempt + 1))


def get_calendar(api, start_date, end_date):
    """确定统一的最新可用截止日。"""
    now = pd.Timestamp.now(
        tz="Asia/Shanghai"
    ).tz_localize(None)

    closed = now.normalize()

    if now.hour < 15:
        closed -= pd.Timedelta(days=1)

    target = (
        pd.Timestamp(end_date).normalize()
        if end_date else closed
    )
    target = min(target, closed)

    start = pd.Timestamp(start_date).strftime("%Y%m%d")

    if pd.Timestamp(start) > target:
        raise ValueError("开始日期晚于截止日期。")

    probe_start = max(
        pd.Timestamp(start),
        target - pd.Timedelta(days=45)
    )

    probe = query(
        api.get_price,
        INDEX_CODE,
        start_date=probe_start.strftime("%Y%m%d"),
        end_date=target.strftime("%Y%m%d"),
        fre_step="1d",
        fields=["close"],
        skip_paused=False,
        fq=None,
        bar_count=0,
        is_panel=False
    )

    if (
        not isinstance(probe, pd.DataFrame)
        or "close" not in probe
        or probe["close"].dropna().empty
    ):
        raise ValueError("近期指数行情为空，请检查数据权限。")

    dates = pd.DatetimeIndex(
        pd.to_datetime(
            probe.loc[probe["close"].notnull()].index
        )
    ).normalize()

    dates = dates[
        (dates >= pd.Timestamp(start))
        & (dates <= target)
    ]

    if len(dates) == 0:
        raise ValueError("所选区间没有可用日线。")

    end = dates.max().strftime("%Y%m%d")

    days = pd.DatetimeIndex(
        query(
            api.get_trade_days,
            start_date=start,
            end_date=end
        )
    ).normalize()

    days = days[
        (days >= pd.Timestamp(start))
        & (days <= pd.Timestamp(end))
    ].sort_values().unique()

    if len(days) == 0 or days[-1] != pd.Timestamp(end):
        raise ValueError("交易日历不完整。")

    return start, end, days


def get_member_ranges(api, days):
    """
    逐日查询真实成分股，但只保存每只股票的入选区间，
    不创建几十万行的全量成员表。
    """
    active = {}
    ranges = {}
    count_issues = []
    expected_rows = 0

    for i, day in enumerate(days):
        codes = query(
            api.get_index_stocks,
            INDEX_CODE,
            date=day.strftime("%Y%m%d")
        )

        if not isinstance(codes, (list, tuple)) or not codes:
            raise ValueError(
                "{}：成分股为空或格式错误。".format(day)
            )

        current = set(codes)

        if len(current) != len(codes):
            raise ValueError(
                "{}：成分股代码重复。".format(day)
            )

        if any(
            not isinstance(symbol, str)
            or not symbol.endswith((".SH", ".SZ"))
            for symbol in codes
        ):
            raise ValueError(
                "{}：成分股代码格式错误。".format(day)
            )

        expected_rows += len(current)

        if len(current) != 300:
            count_issues.append(
                (day.strftime("%Y-%m-%d"), len(current))
            )

        # 当天调出的股票，上一交易日是本段最后一天
        for symbol in list(active):
            if symbol not in current:
                ranges.setdefault(symbol, []).append(
                    (active.pop(symbol), i - 1)
                )

        # 当天新调入的股票
        for symbol in current:
            if symbol not in active:
                active[symbol] = i

        if i == 0 or (i + 1) % 50 == 0 or i + 1 == len(days):
            print(
                "历史成分：{}/{}个交易日".format(
                    i + 1, len(days)
                ),
                flush=True
            )

    # 截止日仍在指数中的股票
    for symbol, first in active.items():
        ranges.setdefault(symbol, []).append(
            (first, len(days) - 1)
        )

    return ranges, expected_rows, count_issues


def prepare_stock(frame, symbol, ranges, days):
    """只保留股票实际属于沪深300的交易日。"""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(
            "{}：整只股票行情为空。".format(symbol)
        )

    if not {"close", "is_paused"}.issubset(frame.columns):
        raise ValueError(
            "{}：缺少close/is_paused字段。".format(symbol)
        )

    quote = frame[["close", "is_paused"]].copy()

    quote.index = pd.DatetimeIndex(
        pd.to_datetime(quote.index)
    ).normalize()

    if quote.index.duplicated().any():
        raise ValueError(
            "{}：行情日期重复。".format(symbol)
        )

    for field in ["close", "is_paused"]:
        quote[field] = pd.to_numeric(
            quote[field], errors="raise"
        )

    if quote["close"].isin(
        [float("inf"), -float("inf")]
    ).any():
        raise ValueError(
            "{}：价格包含无穷值。".format(symbol)
        )

    if not quote["is_paused"].dropna().isin([0, 1]).all():
        raise ValueError(
            "{}：停牌标记不是0/1。".format(symbol)
        )

    expected = pd.DatetimeIndex([
        day
        for left, right in ranges[symbol]
        for day in days[left:right + 1]
    ])

    # 成员缺价仍保留记录，价格留空，不填零
    quote = quote.reindex(expected)

    quote.columns = ["close_pre", "is_paused"]
    quote.insert(0, "symbol", symbol)
    quote.insert(
        0,
        "date",
        expected.strftime("%Y-%m-%d")
    )

    quote["missing_close"] = quote["close_pre"].isnull()

    return quote.reset_index(drop=True)[COLUMNS]


def run_to_csv(start_date=START_DATE, end_date=None):
    import mindgo_api as api

    if BATCH_SIZE < 1:
        raise ValueError("BATCH_SIZE必须大于0。")

    start, end, days = get_calendar(
        api, start_date, end_date
    )

    print(
        "取数区间：{} ~ {}；交易日数：{}".format(
            start, end, len(days)
        ),
        flush=True
    )
    print(
        "全部股票统一截止{}，使用fq='pre'。".format(end),
        flush=True
    )

    ranges, expected_rows, count_issues = get_member_ranges(
        api, days
    )

    symbols = sorted(ranges)

    # 每次运行生成新文件，避免覆盖或混入旧结果
    stamp = pd.Timestamp.now(
        tz="Asia/Shanghai"
    ).strftime("%Y%m%d_%H%M%S_%f")

    filename = "csi300_close_pre_{}_{}_{}.csv".format(
        start, end, stamp
    )

    print(
        "历史累计股票数：{}；应有记录数：{}".format(
            len(symbols), expected_rows
        ),
        flush=True
    )
    print("输出文件：{}".format(filename), flush=True)

    # BOM和表头只写一次
    header = "\ufeff" + ",".join(COLUMNS) + "\n"

    header_written = api.write_file(
        filename,
        header,
        append=False
    )

    if (
        isinstance(header_written, int)
        and header_written != len(header.encode("utf-8"))
    ):
        raise RuntimeError(
            "表头写入字节数不一致，已停止：{}".format(filename)
        )

    written_rows = 0
    missing_rows = 0

    for offset in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[offset:offset + BATCH_SIZE]

        # 每批均使用相同起止日期，保持前复权查询口径一致
        raw = query(
            api.get_price,
            batch if len(batch) > 1 else batch[0],
            start_date=start,
            end_date=end,
            fre_step="1d",
            fields=["close", "is_paused"],
            skip_paused=False,
            fq="pre",
            bar_count=0,
            is_panel=False
        )

        if isinstance(raw, pd.DataFrame) and len(batch) == 1:
            raw = {batch[0]: raw}

        if (
            not isinstance(raw, dict)
            or any(symbol not in raw for symbol in batch)
        ):
            raise ValueError(
                "行情接口遗漏股票或返回格式错误；"
                "已写入{}行，文件{}。".format(
                    written_rows, filename
                )
            )

        parts = [
            prepare_stock(
                raw[symbol], symbol, ranges, days
            )
            for symbol in batch
        ]

        frame = pd.concat(
            parts,
            ignore_index=True
        ).sort_values(["date", "symbol"])

        if frame.duplicated(["date", "symbol"]).any():
            raise ValueError("当前批次日期和股票代码重复。")

        batch_missing = int(
            (
                frame["missing_close"]
                | frame["is_paused"].isnull()
            ).sum()
        )

        # 只转换当前批次；后续批次不再写表头
        content = frame.to_csv(
            index=False,
            header=False
        )

        try:
            written = api.write_file(
                filename,
                content,
                append=True
            )

            if (
                isinstance(written, int)
                and written != len(content.encode("utf-8"))
            ):
                raise RuntimeError(
                    "接口返回的写入字节数与本批次不一致。"
                )

        except Exception:
            # 不自动重试追加，防止写入成功但响应失败导致重复
            print(
                "本批次写入未确认，已停止。"
                "文件：{}；此前已确认{}行。".format(
                    filename, written_rows
                ),
                flush=True
            )
            raise

        written_rows += len(frame)
        missing_rows += batch_missing

        print(
            "已写入股票：{}/{}；累计{}行；缺行情{}行".format(
                min(offset + BATCH_SIZE, len(symbols)),
                len(symbols),
                written_rows,
                missing_rows
            ),
            flush=True
        )

        # 每批写完释放，不累积全量行情
        del raw, parts, frame, content

    if written_rows != expected_rows:
        raise RuntimeError(
            "总行数不一致：应有{}，已写{}。".format(
                expected_rows, written_rows
            )
        )

    summary = {
        "filename": filename,
        "start": start,
        "actual_end": end,
        "fq": "pre",
        "trading_days": len(days),
        "historical_symbols": len(symbols),
        "expected_rows": expected_rows,
        "written_rows": written_rows,
        "missing_quote_rows": missing_rows,
        "constituent_count_issues": count_issues,
        "data_checks_passed": bool(
            missing_rows == 0 and not count_issues
        )
    }

    print(
        "\n全部批次写入完成：{}".format(filename),
        flush=True
    )
    print(
        "总记录数：{}；缺行情：{}；"
        "成分数量异常日期：{}".format(
            written_rows,
            missing_rows,
            len(count_issues)
        ),
        flush=True
    )

    if missing_rows or count_issues:
        print(
            "存在缺失行情或成分数量异常，"
            "请核查后再用于分析。",
            flush=True
        )

    print("文件保存在同花顺研究环境目录。", flush=True)

    return summary


# 直接开始全量取数，并分批写入同一个CSV
download_summary = run_to_csv()