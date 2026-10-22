"""SuperMind全量纯内存版：2015年至最新可用日线，历史每日实际成员，前复权。"""
import time
import pandas as pd

START_DATE = "20150101"
BATCH_SIZE = 10
REQUEST_INTERVAL = 0.2
RETRIES = 3
INDEX_CODE = "000300.SH"


def request(func, *args, **kwargs):
    for attempt in range(RETRIES):
        try:
            result = func(*args, **kwargs)
            time.sleep(REQUEST_INTERVAL)
            return result
        except Exception as exc:
            if attempt + 1 == RETRIES:
                raise
            print("接口重试：{}".format(exc))
            time.sleep(2 ** (attempt + 1))


def check_members(codes, day):
    if not isinstance(codes, (list, tuple)) or not codes:
        raise ValueError("{}：历史成分为空或格式错误，请检查数据权限。".format(day))
    if len(codes) != len(set(codes)):
        raise ValueError("{}：历史成分有重复代码。".format(day))
    if any(not isinstance(s, str) or not s.endswith((".SH", ".SZ")) for s in codes):
        raise ValueError("{}：成分股代码格式错误。".format(day))
    return sorted(codes)


def normalize_quote(frame, symbol):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("{}：行情为空或格式错误。".format(symbol))
    if not {"close", "is_paused"}.issubset(frame.columns):
        raise ValueError("{}：行情缺少close/is_paused字段。".format(symbol))
    quote = frame[["close", "is_paused"]].copy()
    quote.columns = ["close_pre", "is_paused"]
    quote.insert(0, "symbol", symbol)
    quote.insert(0, "date", pd.DatetimeIndex(pd.to_datetime(frame.index)).strftime("%Y-%m-%d"))
    if quote.duplicated(["date", "symbol"]).any():
        raise ValueError("{}：日线日期重复。".format(symbol))
    for field in ["close_pre", "is_paused"]:
        quote[field] = pd.to_numeric(quote[field], errors="raise")
    if quote["close_pre"].isin([float("inf"), -float("inf")]).any():
        raise ValueError("{}：收盘价含无穷值。".format(symbol))
    if not quote["is_paused"].dropna().isin([0, 1]).all():
        raise ValueError("{}：停牌标记不是0/1。".format(symbol))
    return quote


def run(start_date=START_DATE, end_date=None, api=None):
    if api is None:
        import mindgo_api as api
    if BATCH_SIZE < 1:
        raise ValueError("BATCH_SIZE必须是正整数。")
    now = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None)
    latest_closed = now.normalize()
    if now.hour < 15:
        latest_closed -= pd.Timedelta(days=1)
    target = min(pd.Timestamp(end_date).normalize() if end_date else latest_closed, latest_closed)
    start = pd.Timestamp(start_date).strftime("%Y%m%d")
    if pd.Timestamp(start) > target:
        raise ValueError("开始日期晚于截止日期。")

    # 探测平台最新可用日线；指数可用后，仍会核查每只成员股的价格覆盖。
    probe = request(api.get_price, INDEX_CODE,
                    start_date=max(pd.Timestamp(start), target - pd.Timedelta(days=45)).strftime("%Y%m%d"),
                    end_date=target.strftime("%Y%m%d"), fre_step="1d", fields=["close"],
                    skip_paused=False, fq=None, bar_count=0, is_panel=False)
    if not isinstance(probe, pd.DataFrame) or "close" not in probe or probe["close"].dropna().empty:
        raise ValueError("近期指数行情为空，请检查数据权限。")
    available = pd.DatetimeIndex(pd.to_datetime(probe.loc[probe["close"].notnull()].index)).normalize()
    available = available[(available >= pd.Timestamp(start)) & (available <= target)]
    if len(available) == 0:
        raise ValueError("所选区间没有可用日线。")
    end = available.max().strftime("%Y%m%d")
    days = pd.DatetimeIndex(request(api.get_trade_days, start_date=start, end_date=end)).normalize()
    days = days[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))].sort_values().unique()
    if len(days) == 0 or days[-1] != pd.Timestamp(end):
        raise ValueError("交易日历未覆盖所选区间。")
    print("取数区间：{} ~ {}；交易日数：{}".format(start, end, len(days)), flush=True)
    print("所有股票统一查询至{}，fq='pre'（前复权）。".format(end), flush=True)

    records = []
    for i, day in enumerate(days, 1):
        codes = check_members(request(api.get_index_stocks, INDEX_CODE, date=day.strftime("%Y%m%d")), day)
        date_text = day.strftime("%Y-%m-%d")
        records.extend((date_text, code) for code in codes)
        if i == 1 or i % 50 == 0 or i == len(days):
            print("历史成分：{}/{}个交易日".format(i, len(days)), flush=True)
    members = pd.DataFrame(records, columns=["date", "symbol"])
    del records
    symbols = sorted(members["symbol"].unique())
    print("历史累计股票数：{}（包含历史调入、调出股票）".format(len(symbols)), flush=True)
    counts = members.groupby("date").size()
    pieces = []
    for offset in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[offset:offset + BATCH_SIZE]
        raw = request(api.get_price, batch if len(batch) > 1 else batch[0],
                      start_date=start, end_date=end, fre_step="1d", fields=["close", "is_paused"],
                      skip_paused=False, fq="pre", bar_count=0, is_panel=False)
        if isinstance(raw, pd.DataFrame) and len(batch) == 1:
            raw = {batch[0]: raw}
        if not isinstance(raw, dict) or any(s not in raw for s in batch):
            raise ValueError("行情接口遗漏股票或返回格式错误。")
        quotes = pd.concat([normalize_quote(raw[s], s) for s in batch], ignore_index=True)
        if quotes.duplicated(["date", "symbol"]).any():
            raise ValueError("行情存在重复的日期和股票代码。")
        # 左连接保留成员缺价记录；非成员期间的行情不放入结果。
        batch_members = members[members["symbol"].isin(batch)]
        if batch_members.duplicated(["date", "symbol"]).any():
            raise ValueError("成分名单存在重复的日期和股票代码。")
        part = batch_members.merge(quotes, on=["date", "symbol"], how="left")
        if len(part) != len(batch_members):
            raise ValueError("合并后成员记录数发生变化。")
        pieces.append(part)
        print("行情：{}/{}只历史股票".format(min(offset + BATCH_SIZE, len(symbols)), len(symbols)), flush=True)
    long = pd.concat(pieces, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    long["missing_close"] = long["close_pre"].isnull()
    if len(long) != len(members) or long.duplicated(["date", "symbol"]).any():
        raise ValueError("结果行数或唯一性检查失败。")
    missing = long[long["missing_close"] | long["is_paused"].isnull()]
    wide = long.pivot(index="date", columns="symbol", values="close_pre").reindex(columns=symbols)
    membership = members.assign(is_member=1).pivot(index="date", columns="symbol", values="is_member")
    membership = membership.reindex(index=wide.index, columns=symbols).fillna(0).astype(int)
    summary = dict(start=start, actual_end=end, fq="pre", generated_at=str(now),
                   trading_days=len(days), historical_symbols=len(symbols), rows=len(long),
                   missing_quote_rows=len(missing), non_300_days=int((counts != 300).sum()),
                   data_checks_passed=bool(missing.empty and (counts == 300).all()))
    print("统计：", summary)
    print("前20行（全量结果保留在close_long变量中）：")
    print(long.head(20).to_string(index=False))
    print("最后20行：")
    print(long.tail(20).to_string(index=False))
    check = long[(long["date"] == "2015-01-05") & (long["symbol"] == "000001.SZ")]
    if not check.empty:
        print("平安银行2015-01-05前复权价格核对（统一查询截止日{}）：".format(end))
        print(check.to_string(index=False))
    if not summary["data_checks_passed"]:
        print("存在缺失行情或成分数量异常，请先检查missing_quotes和constituent_count_issues。")
    return long, wide, membership, summary


if __name__ == "__main__":
    close_long, close_wide, membership_wide, summary = run()
    missing_quotes = close_long[close_long["missing_close"] | close_long["is_paused"].isnull()]
    daily_constituent_count = close_long.groupby("date").size()
    constituent_count_issues = daily_constituent_count[daily_constituent_count != 300]
