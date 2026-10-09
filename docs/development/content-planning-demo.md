# 内容规划 Demo 验证记录

## Step 1：全量输入与确定性规则（2026-10-09）

采用 content-strategy、seo-audit、site-architecture、b2b-inquiry-website 的相关方法，固化为版本化代码。主题按照产品对象与客户任务归集；主题不是已经通过 SERP 验证的独立页面。

真实三份 XLSX：90,009 个原始记录，56,457 个独立关键词，33,549 条重复来源，3 条无效记录；基础词 14 个。冲突指标置为未知，保留原始观察。规则扩展检索短语与已测量关键词分开。

验证：`PAGGGLE_VERIFY_FIXED_DATA=1 PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests/test_content_strategy_fixed.py`：2 passed。覆盖全量对账、重复计算哈希一致、分配无丢失、原文引用可定位、未读 URL 阻止直接新增、优化/新增交付结构。

页面覆盖是定位正文依据的可复核规则检查，不能等同专业内容质量审查。业务词典专用于本次硅胶行业 Demo；未匹配词保留待归类，不宣称通用行业聚类完成。原有数据与人工核对未改写。

## Step 2：持久化流程、项目隔离与页面解析

新增按项目配置的 `content_workflows`：`seed_file`、`keyword_files`、`competitor_urls`、`own_evidence_urls`、`market`、`language`；本地配置不入库、不返回浏览器。输入路径限定在已配置的 `backend/tests/data`，浏览器只能选择允许的文件名。

v9 仅新增 `content_runs` / `content_assets`。五阶段运行复用既有项目任务锁和取消机制；输入快照、规则版本及结果指纹独立保存。重放不读取当前文件、不访问网络，比较输出哈希；规则改变时需创建新规划，旧版本保持可查看。重启中断任务由已有任务恢复机制标记失败，不能伪装成完成。

真实竞品解析修复：NEWTOP HTML 有文档标题与 SVG 内 `reCAPTCHA` 标题；原解析器扫描所有 title，导致正常页面被误判。改为检查 document head 的 title。以实际抓取 HTML 回归验证，仍保留真实挑战页拒收。

规则 v3 修正：URL 中的客户任务优先于标题营销词；一般制造服务不误匹配厨房产品页；短标题不算完整回答；优化建议按规格/材料/采购/询盘分别提供。竞品始终标为参考，不能当本站能力。

验证：真实数据专项 6 passed（全量对账、规则重放、实际 HTML 解析、页面匹配、HTTP 项目隔离/导出/分页/无网络重放、取消及重复任务）；采集回归 31 passed；既有后端 98 passed，16 项需要私有资料的测试默认跳过。私有副本测试前后来源、画像、基础词和首次筛选状态一致。


## Step 3：真实浏览器与最终结果

原项目 8000 的“内容规划”页面发起完整运行。v3 原运行 `44c7bca57fab40ffa61bae2e0b3e4645`，界面重放 `9e301b5d12cd4a1483ed66059d65b196`，均成功：

- 输入 SHA-256：`134499ef1830bd3ed2af588da4cbbbdec1bad99ac0a3bd70bed112eedb66f03a`
- 结果 SHA-256：`4df96f0e5c9355ed6c6ae45bbd43764595e93910277b1181bcfca6d9c4cd8b4a`
- 133 主题：12 保留、12 优化、48 新增候选、61 待核对。25,993 词入主题，30,464 词保留待归类原因。
- 本站 26/388 页已读；三个竞品 6/6 选定页已读。本站 sanitize-silicone-teethers 补读仍被真实验证/跳转页拦截；不得据此判断没有原有内容。

Chrome：网页按钮发起、阶段进度、主题详情及原文、优化建议、10/50/100 行明细、无编辑关闭、刷新恢复、按快照重放；390px 页面 scrollWidth=390，表格转卡片保留字段标签。既有前端 5 项与 JS 语法通过。控制台仅一条资源 404，未发现 JavaScript 异常。完整结果导出的内容和项目隔离由 HTTP 测试验证。

原库与 `data/backups/before-content-plan-20261009T170058.sqlite3` 比较，projects、sources、profiles、seed_keyword_runs、seed_keyword_revisions、demands、intake_batches、cluster_runs、keyword_onboarding 的内容哈希全相同；完整性 ok、外键无错误。测试报告/快照/HTML/备份均位于忽略的 data/，不入公开 Git。

边界：这是硅胶行业的确定性规则 Demo，不是通用语义聚类或自动发布系统。大量词仍需人工归类/业务核对；正文覆盖规则只能支持可复核初筛。竞品研究覆盖选定页面，未读取全站；新增候选需继续检查 SERP 与全文相似内容。
