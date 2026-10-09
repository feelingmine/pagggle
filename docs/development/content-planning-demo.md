# 内容规划 Demo 验证记录

## Step 1：全量输入与确定性规则（2026-10-09）

采用 content-strategy、seo-audit、site-architecture、b2b-inquiry-website 的相关方法，固化为版本化代码。主题按照产品对象与客户任务归集；主题不是已经通过 SERP 验证的独立页面。

真实三份 XLSX：90,009 个原始记录，56,457 个独立关键词，33,549 条重复来源，3 条无效记录；基础词 14 个。冲突指标置为未知，保留原始观察。规则扩展检索短语与已测量关键词分开。

验证：`PAGGGLE_VERIFY_FIXED_DATA=1 PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests/test_content_strategy_fixed.py`：2 passed。覆盖全量对账、重复计算哈希一致、分配无丢失、原文引用可定位、未读 URL 阻止直接新增、优化/新增交付结构。

页面覆盖是定位正文依据的可复核规则检查，不能等同专业内容质量审查。业务词典专用于本次硅胶行业 Demo；未匹配词保留待归类，不宣称通用行业聚类完成。原有数据与人工核对未改写。
