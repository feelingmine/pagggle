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
