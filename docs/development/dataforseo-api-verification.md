# DataForSEO API 实测（2026-10-09）

结论：认证和请求成功，但指定关键词在本次 Google Ads Keywords For Keywords 调用中没有可用指标，也没有扩展词。不能把返回成功当作数据质量通过，不能把 null 当作 0。单个关键词不足以判断整个供应商的覆盖率或准确率。

## 请求与结果

- 原项目：`3569ed46f2554178b327c2e7c58c8b3b`。
- 用户指定关键词：`silicone baby feeding set`；美国 `2840`、英语 `en`，按 `search_volume` 排序。
- 共发起两次 POST：先按用户提供的 `/live.ai` 调用，再以相同参数调用 `/live`，检查被裁剪的原始字段；没有自动重试。
- 请求时间分别为 UTC 13:25:23、13:25:56（北京时间 21:25:23、21:25:56）。

| 检查项 | `/live.ai` | `/live` |
| --- | --- | --- |
| HTTP / API 状态 | 200 / 20000 | 200 / 20000；任务状态 20000 |
| 实测耗时 | 4.849 秒 | 5.256 秒 |
| 返回关键词数量 | 1 | 1 |
| 新增扩展词 / 重复词 | 0 / 0 | 0 / 0 |
| search_volume | 字段缺失 | null |
| cpc | 字段缺失 | null |
| competition / competition_index | 字段缺失 | 均为 null |
| monthly_searches | 字段缺失 | null |
| low/high_top_of_page_bid | 字段缺失 | 均为 null |
| 响应中的费用 | 未提供 | 0.09 USD |

`.ai` 的实际响应只有 id、状态和一个仅含 keyword 的 items 元素。标准响应确认市场和语言正确，search_partners=false，但指标全部缺失。本次无法验证趋势新鲜度、数值准确率或扩展词相关性。`.ai` 未提供费用，不能把标准调用的 0.09 USD 写成两次调用的总费用。

## 现有 Semrush 数据交叉检查

只读遍历用户已授权的三份 2026-10-09 美国 XLSX 文件：`silicone-baby-feeding-set`、`silicone-baby-products`、`silicone-teether`。每份均找到 1 条完全同名词：Volume=390、Keyword Difficulty=7、CPC (USD)=0、Competitive Density=0。

这说明现有 Semrush 文件有该词指标，而本次 DataForSEO 响应没有；不是“390 对 0”的差异。三份文件来自同一供应商，不构成三个独立真值。未获得 Google Ads 原始账户数据，无法确认 Semrush 数值准确性或判定 DataForSEO 缺失原因。

本次结果不足以支持替换现有 Semrush 数据源。若后续接入，应保留未知值，并将接口成功和数据可用性分别校验。

## 复现与保存

根目录忽略文件 `config.json` 新增 `dataforseo_login`、`dataforseo_password`；配置模型以 SecretStr 保存，示例配置仅包含空值。原示例 Basic 头是 login:password 占位符，脚本使用本地配置生成实际认证。

```sh
# 每次执行会重新请求，可能产生费用。
PYTHONPATH=backend .venv/bin/python backend/scripts/probe_dataforseo.py
PYTHONPATH=backend .venv/bin/python backend/scripts/probe_dataforseo.py --full-response
```

原始响应和请求摘要位于忽略目录 `data/verification/3569ed46f2554178b327c2e7c58c8b3b/dataforseo/`：

- `20261009T132523224678Z/response.txt`、`request-summary.json`、`semrush-comparison.json`。
- `20261009T132556524046Z/response.txt`、`request-summary.json`。

验证：现有配置保密测试 1 passed；实际本地配置加载成功，新凭据在 repr 和 JSON 序列化中均被遮罩；`git diff --check` 通过。未修改正式数据库、网站分析、导入文件或人工编辑记录。没有创建合成项目或测试数据。

官方说明：[标准接口字段](https://docs.dataforseo.com/v3/keywords_data-google_ads-keywords_for_keywords-live/)、[AI 响应裁剪规则](https://docs.dataforseo.com/v3/appendix/ai_optimized_response/)。其中 competition 是广告竞争度，不是 SEO Keyword Difficulty；`.ai` 是响应格式裁剪，不代表 AI 生成了关键词指标。
