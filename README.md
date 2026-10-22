# 沪深300历史每日实际成分股的前复权收盘价

**当前为 SuperMind Python 3.8 全量纯内存版，保留旧 pandas 兼容写法，不执行任何文件读写。** 用户实测平台拒绝 `pathlib` 和普通 `open()`，因此已移除路径模块、目录创建、文件缓存、CSV保存。脚本只导入 `time`、`pandas` 和平台的 `mindgo_api`。空值判断使用旧版可用的 `notnull()` / `isnull()`；合并时不用旧版缺少的 `validate` 参数，改为手工检查键唯一性和合并前后行数。

在用户提供的 [Notebook](https://supermind.10jqka.com.cn/notebook/user/885704074/notebooks/Untitled.ipynb?kernel_name=python3#) 新建代码单元，将 `csi300_history.py` 的全部内容复制进去，按 Shift+Enter。

直接运行全部代码即可获取2015-01-01至平台最新可用的已收盘日线。已移除测试开关，不会再默认只取2015年最初5个交易日。

## 内存结果

- `close_long`：全量历史成员日线长表，字段为 `date,symbol,close_pre,is_paused,missing_close`。
- `close_wide`：行是交易日，列是历史股票并集，非成分期间留空。
- `membership_wide`：同维度的0/1矩阵，1表示当日成分股。
- `summary`：实际截止日、交易日数、历史股票数、成员记录数、缺失和异常数量。
- `missing_quotes`：成员当日缺价或缺停牌标记的记录。
- `daily_constituent_count` / `constituent_count_issues`：每日成分数与数量不等于300的日期。

代码直接打印统计和前20行。全量结果在变量中，可在下一单元格查询，例如：

```python
close_long.tail(20)
close_long[close_long['symbol'] == '600519.SH'].head(20)
missing_quotes.head(20)
```

不必打印全部几十万行。若确实需要直接显示某个年份的全部记录，可限定范围再打印：

```python
print(close_long[close_long['date'].str.startswith('2015')].to_string(index=False))
```

## 口径与限制

每个交易日独立调用 `get_index_stocks('000300.SH', date=当天)`，不把当前300只回填历史。历史累计股票数会超过300。所有股票使用相同起止日期调用 `get_price(..., fq='pre', fre_step='1d')`，仅按股票批次划分，避免拼接不同截止日的前复权数据。具体动态前复权算法以平台为准。

用成员表左连接行情，成员缺价保留并报告，不填零、不自行前向填充。停牌时保留接口价格与 `is_paused` 标记。整只股票返回为空、接口遗漏股票、成分为空或代码重复时停止；部分日期缺价进入结果和异常变量。

运行当天15点前不取当日行情，并通过指数探测平台可用日期。指数可用不保证所有成员行情均已发布，因此保留成员覆盖检查。非300日期按真实返回名单保留并报告，不强行删除或补齐。

首次全量运行需查询几千个交易日的成分名单，可能较久。可调大 `REQUEST_INTERVAL` 应对限流。纯内存版不再提供文件缓存或持久化续跑；内核重启后变量会丢失，需要重新取数。

已依据官方文档核对函数签名，并以模拟接口验证数据处理与禁止文件访问情况下的运行。未宣称已通过平台全量真实行情验证。

官方文档：[API](https://quant.10jqka.com.cn/view/help/4)、[研究环境与系统模块限制](https://quant.10jqka.com.cn/view/help/14)。

## 前复权截止日核对

用户已在平台实测000001.SZ于2015-01-05的收盘价：不复权16.02；前复权查询截止2015-01-09仍为16.02；前复权查询截止2026-10-22为7.34。因此最终代码只分股票批次，所有前复权请求统一使用最新截止日，不再以2015年的测试截止日取价。7.34为当次接口实际返回，不代表已证实与终端显示的7.35完全一致。
