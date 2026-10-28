# 沪深300指数 代码000300，从2015年到目前的日线收盘价数据
from mindgo_api import get_price, get_trade_days, write_file
from datetime import datetime, timedelta, timezone
import pandas as pd

SYMBOL = "000300.SH"        # 沪深300指数
START_DATE = "2015-01-01"

# 使用北京时间；当天15:00前排除当天，避免导出未收盘的日线。
now_cn = datetime.now(timezone(timedelta(hours=8)))
end_day = now_cn.date()

if now_cn.hour < 15:
    end_day -= timedelta(days=1)

end_date = end_day.strftime("%Y-%m-%d")

# 一、按年分批获取数据
parts = []

for year in range(2015, end_day.year + 1):
    begin = "{}0101".format(year)
    end = min("{}1231".format(year), end_day.strftime("%Y%m%d"))

    raw = get_price(
        securities=SYMBOL,
        start_date=begin,
        end_date=end,
        fre_step="1d",
        fields=["close"],
        skip_paused=False,
        fq=None,           # 原始指数收盘点位
        bar_count=0,
        is_panel=False
    )

    if raw is None:
        raise RuntimeError(
            "{}年查询返回 None，请检查平台提示。".format(year)
        )

    if raw.empty:
        continue

    frame = raw[["close"]].copy()
    frame.insert(
        0,
        "date",
        pd.to_datetime(frame.index).strftime("%Y-%m-%d")
    )

    parts.append(frame.reset_index(drop=True))
    print("{}年获取 {} 条".format(year, len(frame)))

if not parts:
    raise RuntimeError("未获取到数据，请检查代码、日期或数据权限。")

# 二、合并所有年份，按日期排序并去重
close_long = pd.concat(parts, ignore_index=True)

close_long = close_long[
    (close_long["date"] >= START_DATE)
    & (close_long["date"] <= end_date)
]

close_long = (
    close_long.sort_values("date")
    .drop_duplicates(subset=["date"], keep="last")
    .reset_index(drop=True)
)

if close_long.empty:
    raise RuntimeError("指定日期范围内没有数据。")

# 三、检查收盘价
close_long["close"] = pd.to_numeric(
    close_long["close"],
    errors="raise"
)

if close_long["close"].isnull().any() or (close_long["close"] <= 0).any():
    raise RuntimeError("发现空值或异常收盘价，请检查后重试。")

# 四、对照交易日历，检查历史数据是否缺失
trade_days = get_trade_days(
    start_date=START_DATE.replace("-", ""),
    end_date=end_date.replace("-", "")
)

expected = set(pd.to_datetime(trade_days).strftime("%Y-%m-%d"))
missing = sorted(expected - set(close_long["date"]))

today = now_cn.strftime("%Y-%m-%d")
past_missing = [d for d in missing if d < today]

if past_missing:
    raise RuntimeError(
        "历史交易日缺失，请重试：{}".format(", ".join(past_missing))
    )

if today in missing:
    print("提示：平台尚未返回今天的日线，将导出到最近可用交易日。")

# 五、添加指数代码，最终列顺序为 date、code、close
close_long.insert(1, "code", SYMBOL)

# 六、只保存一个合并后的 CSV 文件
latest_date = close_long["date"].iloc[-1]

filename = "csi300_close_2015_to_{}.csv".format(
    latest_date.replace("-", "")
)

# 添加 UTF-8 BOM，方便 Excel 正确识别编码。
content = "\ufeff" + close_long.to_csv(index=False)
written = write_file(filename, content, append=False)

print("\n导出完成")
print("文件名：", filename)
print("起始日期：", close_long["date"].iloc[0])
print("截止日期：", latest_date)
print("总记录数：", len(close_long))
print("写入字节数：", written)
print("\n最后5条数据：")
print(close_long.tail().to_string(index=False))