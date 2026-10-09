# pagggle

Page generated for Google：面向外贸独立站的、以事实为基础的 AI 内容运营。

## 目录

```text
docs/
  product/          产品定义、PRD（需求基线）
  development/      技术决策、分步验收与开发记录
backend/
  pagggle/          Python 服务
  tests/            自动化测试
  pyproject.toml    后端依赖与测试配置
frontend/           浏览器界面
config.example.json 可提交的配置示例，无真实凭据
config.json         本地实际配置，不提交
data/               本地数据库及运行资料，不提交
```

按 PRD 的 M1 → M2 → M3 逐步实现、验证。当前范围及验证结果见 [开发记录](docs/development/progress.md)。

业务流、点击流和操作路径见 [UX 方案](docs/product/design/UX方案-V0.1.md)。前端已按用户选定的第二张设计实现阶段版本，完整验证状态见 [设计 QA](docs/development/design-qa.md)。

所有配置统一读取根目录 `config.json`。模型密钥只用于服务端鉴权，不发送到前端，不写入普通日志或模型提示词。当前应用限定本机使用，尚不具备多用户登录和租户授权。

## 安装与验证

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r backend/requirements.txt
cd backend
../.venv/bin/python -m pytest -q
```

不要将真实配置、数据库、采集正文或客户资料提交到代码仓库。

## 本地启动

在具有本地端口监听权限的终端执行：

```sh
cd backend
../.venv/bin/python -m pagggle
```

默认监听 `127.0.0.1:8000`，由根目录 `config.json` 配置。现阶段仅供本机试点，不可直接对公网部署。

## 网站优化入口

打开 `http://127.0.0.1:8000/#start`：输入网站 → 发现 sitemap 链接 → 按地图或路径筛选并勾选 → 采集选中页面 → 查看原文 → 手动生成业务理解。发现阶段不提取正文，采集阶段不调用模型；当前仍等待用户验证解析结果，暂停后续意图与分组推进。

页面数量默认不限（`crawl_max_pages: null`），没有默认勾选。数量上限只约束本轮选择，超出会提示调整，不会静默截掉清单或正文。模型按 `model_input_chars` 分批读取已存完整文本，记录实际分析来源和批次；失败批次不会保存不完整新画像。已读快照和人工修订版本保留，历史截取版本不会自动变成完整版本。

## 采集配置与网络排查

`crawl_backend` 统一控制 robots.txt、所有层级的 XML sitemap 和网页正文，不会在失败时静默切换通道。默认 `"direct"` 使用本机连接，使用 `request_timeout_seconds` 与 `crawl_dns`；`crawl_dns: "system"` 使用系统 DNS。若代理把域名映射成 `198.18.0.0/15` Fake-IP，可在本地配置中设为 `crawl_dns: "cloudflare"`，通过加密 DNS 获取实际地址，继续校验公网范围。direct 模式还会固定连接地址。参照 [Cloudflare DNS JSON 接口](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)。

站点直连返回验证码中转页时，可选择 `crawl_backend: "firecrawl"`，并设置独立的 `firecrawl_api_key`；当前本地试点使用此配置，robots 和 sitemap 同样经 Firecrawl 读取。该方式只将采集 URL 发送到 [Firecrawl Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)，不发送模型凭据或企业补充资料；使用账户采集额度，仍可能遇到服务限流或目标站点限制。`scrape_timeout_seconds` 控制单页时限，`crawl_max_pages` 设置采集数量默认值，界面可对本轮覆盖该值，默认 null 为不限；不会限制 sitemap 链接发现数量。发现阶段只保存地图及页面链接，用户筛选后才采集正文。返回内容保存在当前项目的本地数据库，验证码页和异常响应不作为业务依据。

## 关键词聚类

入口：`http://127.0.0.1:8000/#clusters`，也可从顶部“关键词分析”进入；已完成首次筛选的项目从“内容需求 → 查看关键词分析”进入。按照 [两阶段 engine 设计](docs/development/hybrid_seo_keywords_clustering_engine.md) 实现，具体边界和验证见 [实现记录](docs/development/keyword-clustering-implementation.md)。

首次安装依赖后，在项目根目录准备公共本地模型：

```sh
.venv/bin/python scripts/prepare-clustering-model.py
```

首次路径：导入关键词 → 预览并确认 → 分析全部有效关键词 → 检索组内成员/状态并选择候选组 → 保存筛选，完成首次分析。首次完成后才显示客户问题与产品变化入口；完成状态及候选选择按项目和分析版本保存。点击主词可查看成员、主次词及重合 URL。语义编码在本地进行，不调用付费模型或实时搜索接口；结果独立于网站解析验收，可先验证关键词算法。

支持 CSV、单工作表 XLSX 和逐行关键词。列名兼容 `Keyword,Search Volume,Keyword Difficulty,SERP Results` 及 `keyword,volume,kd,serp`。SERP 填前 10 条自然结果完整 URL（逗号、单元格换行或 JSON 数组），不是 SERP Features。可补 `market,language,source,data_date,serp_source,serp_date,device`；日期为 YYYY-MM-DD，设备为 desktop/mobile/tablet。普通 Semrush 词表没有 SERP URL 时会保留待补证据。指标未知留空，不填零。

根配置 `clustering_model`、`clustering_model_cache`、`clustering_distance_threshold`、`clustering_serp_threshold` 分别指定本地模型、缓存目录、语义距离阈值（默认 0.65）、最少重合 URL 数（默认 3）。数量与阈值不是经过校准的 SEO 结论；每组仅一个建议主词，所有输出仍待审核，不自动创建页面。每次运行保存新版本，旧结果可查看。

## 需求意图

完成首次关键词筛选后，可增量添加客户问题或产品变化 → 预览与确认导入 → 核对并保存业务理解 → 识别需求意图 → 点击结果查看候选解释、原词依据、推断和未知项。`intent_batch_size` 控制每次模型处理的输入数量，默认 5。失败或遗漏的输入可以重试，成功项保留；歧义允许多个候选，企业适配单独评估。画像更新后，已有结果会提示需要复核，目前尚未实现意图人工修订及后续问题分组。

`model_thinking` 默认 null，不向模型供应商发送此可选参数。当前 DeepSeek 配置使用 `"disabled"` 做结构化提取，并将 `model_max_tokens` 设为 12000，避免思考消耗全部输出预算却没有 JSON 正文；更换供应商时应按其支持情况调整。参数依据 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)。无论是否启用思考，来源、ID、字段和原文片段仍由程序校验。

## 离线设计预览

```sh
python3 scripts/build-ui-preview.py
```

打开生成的 `data/preview/index.html`。此预览只使用合成资料与内存接口，关闭或刷新会重置演示修改；用于业务理解及导入表单展示，不模拟 sitemap 任务或首次关键词全流程。完整新流程需使用 HTTP 服务；离线展示不代表真实模型、采集或发布验证成功。
