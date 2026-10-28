import pandas as pd

# 已下载的原始文件
source = (
    "/Users/huangjiabao/Downloads/"
    "csi300_close_pre_20150101_20261028_20261028_201326_841588.csv"
)

# 全部按字符串读取，保留股票代码和价格的原始文本
data = pd.read_csv(
    source,
    encoding="utf-8-sig",
    dtype=str,
    keep_default_na=False
)

# 对完整文件按日期统计，而不是只查看文件头尾
daily_counts = data.groupby("date")["symbol"].agg(
    ["size", "nunique"]
)
daily_counts.columns = ["记录数", "不同股票数量"]

duplicates = int(
    data.duplicated(["date", "symbol"]).sum()
)

print("总记录数：", len(data))
print("交易日数量：", len(daily_counts))
print("历史累计股票数量：", data["symbol"].nunique())
print("日期范围：", data["date"].min(), "至", data["date"].max())
print("每天最少股票数量：", daily_counts["不同股票数量"].min())
print("每天最多股票数量：", daily_counts["不同股票数量"].max())
print("日期＋股票代码重复记录数：", duplicates)

# 检查每天是否都有300只不同股票，且没有重复记录
abnormal = daily_counts[
    (daily_counts["不同股票数量"] != 300)
    | (daily_counts["记录数"] != 300)
]

if abnormal.empty:
    print("\n检查通过：所有交易日都是300只不同股票。")
else:
    print("\n发现异常日期：")
    print(abnormal.to_string())

print("\n最早5个交易日：")
print(daily_counts.head().to_string())

print("\n最近5个交易日：")
print(daily_counts.tail().to_string())

# 按日期、股票代码排序，不删除记录、不改价格
sorted_data = data.sort_values(
    ["date", "symbol"],
    kind="mergesort"
)

sorted_file = source[:-4] + "_按日期排序.csv"
count_file = source[:-4] + "_每日股票数量.csv"

sorted_data.to_csv(
    sorted_file,
    index=False,
    encoding="utf-8-sig"
)

daily_counts.to_csv(
    count_file,
    encoding="utf-8-sig"
)

print("\n已保存排序后的完整数据：", sorted_file)
print("已保存每日股票数量核对表：", count_file)