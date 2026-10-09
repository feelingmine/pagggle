# Sitemap 读取耗时诊断与直连改造

用户要求网站输入、发现、筛选、采集流程标为待用户验证。2026-10-08 只诊断；2026-10-09 按用户明确要求改为 robots/sitemap 强制直连，以下先保留历史诊断。

**当前策略**：用户随后明确改用 Firecrawl，robots 和所有 sitemap 已恢复遵循 crawl_backend。以下直连记录是历史诊断，最新验证见文末。

一个站点可用 sitemap index 指向多份 sitemap，按产品、文章、分类等拆分；协议也规定单份最多 50000 URL、解压后 50MB。参见 [Sitemaps 协议](https://www.sitemaps.org/protocol.html)。当前采集器仅支持 XML 索引/urlset；RSS 也可作为协议中的输入形式，但当前尚未实现，不能将未支持等同于站点文件无效。

本地只读测量（2026-10-08）：最近发现任务状态 succeeded，created_at 到 updated_at 相差 78.4 秒。对项目中已存成功 XML 分别重复解析 20 次，单份解析中位数约 0.005–1.602ms；最大样本约 176KB。此测量不含网络和写库，不能代表每次网络请求耗时。

代码与配置证据：

- 当前 crawl_backend=firecrawl；robots 与 sitemap 共用网页正文的 Firecrawl /v2/scrape 通道，每次还做公网 DNS 校验。
- 地图逐份串行读取，单请求配置超时 60 秒（这是超时上限，不代表每份实际用了 60 秒）。
- 记录中存在 HTTP 429、无效 XML 和未支持的 RSS；这些也需请求结束后才能报告。
- 尚未记录单份下载、解析、入库三个独立阶段时长，无法确定远端排队、反爬或网络各占多少。

历史优化建议（2026-10-09 用户指令已覆盖回退建议，不允许回退 Firecrawl）：robots/sitemap 轻量直接请求；独立超时和明确失败状态；有界并发、429 退避、缓存/条件请求；前端区分下载等待与解析并渐进展示链接。需要先验证该站点地图能否直连，不能因普通页面曾被拦截就假设所有地图均需重型抓取。


## 2026-10-09 直接请求实现

- robots.txt、默认 sitemap、robots 声明的地图及 sitemap index 子地图统一调用现有直接 HTTP 客户端，独立于 crawl_backend。无 Firecrawl 回退；正文仍按既有配置读取。
- 直连继续使用公网 DNS 校验、固定 IP 连接、TLS 主机名校验、同主机重定向约束和响应大小限制。沿用 request_timeout_seconds，不新增分散配置。
- robots 非 404 失败时停止采集，并将本次错误传入任务状态，避免历史不可变成功快照掩盖当前失败或误报成功。历史来源与人工画像不覆盖。
- 真实站点在独立验证库测得 robots.txt 约 3.847 秒返回 HTTP 202。发现流程停止，正文保存 0、模型调用 0，Firecrawl 入口以测试断言禁止。不能将快速失败当作加速成功。
- 这一步只优化通道选择，未增加并发、缓存或 RSS/gzip 支持。站点直连非 200 的原因仍需进一步排查，完整发现流程仍待用户验证。

- sitemap.xml 单独直连诊断约 4.480 秒，同样 HTTP 202；没有将该响应当作 XML 使用。发现任务因 robots 失败停止，不因单独诊断跳过 robots 规则去采集页面。
- 最终回归 79 passed，2 条既有依赖弃用提示。新增覆盖 Firecrawl 配置下的递归地图直连、失败无回退、正文通道保留，以及已有成功快照时当前 robots 失败仍使任务失败。

## 2026-10-09 HTTP 202 根因确认

目标站点响应含 `SG-Captcha: challenge`、`Content-Type: text/html`，正文是指向 `/.well-known/sgcaptcha/` 的 179 字节中转 HTML。直接客户端使用 Pagggle User-Agent 约 2.29 秒、普通 Chrome User-Agent 约 1.93 秒、系统 curl 约 2.60 秒，均返回 202。由此确认本次响应是 SiteGround Antibot 验证挑战，没有取得 robots 内容；更换普通 User-Agent 未解决，不能解释为后台正在生成 sitemap。

[SiteGround 官方说明](https://www.siteground.com/kb/seeing-captcha-website/)指出，其 Antibot 可能要求 CAPTCHA，完成后可将 IP 或 User-Agent 加入允许范围。因此浏览器完成验证是可尝试的人工恢复路径，仍需重新验证采集器请求；不能保证二者共享放行状态。持续拦截应由站点管理员联系 SiteGround 核查并针对采集来源放行，无需关闭整站防护。本次未访问主机后台、未代发支持请求。

应用已加入专用错误和浏览器验证链接，保留手动重试。两个新增测试确认带挑战头与普通 202 的区别、拒绝读取失败正文且关闭连接；全量 81 passed。服务已重启，实际站点解封和完整地图发现尚未通过，不能将错误识别改进等同于采集恢复。原始诊断响应存于忽略的 data/verification，验证码参数不写入工程文档。

## 恢复 Firecrawl 后的真实验证

按用户最新指令，统一通过配置选择读取通道；本地为 Firecrawl，不再直接请求目标站点的 robots/sitemap。沿用 rawHtml 输出以保留 XML 结构，不调用模型提取地图。[Firecrawl REST 文档](https://docs.firecrawl.dev/agent-source-of-truth/curl)提供该输出格式。

独立验证库实际任务约 58.54 秒，13 次请求。前 10 次远端读取成功（每次约 4–5 秒），其中 robots 与 7 份地图校验通过；另外 2 份不是受支持的有效 XML sitemap。后 3 次返回服务 HTTP 429，失败原因逐份保留。共发现 187 个链接、正文 0、模型调用 0，不代表整站完整覆盖。没有通过限制发现数量规避问题，也没有声称 Firecrawl 能保证免受拦截或限流。
