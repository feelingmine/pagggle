# GitHub 公开发布

2026-10-09，用户明确授权新建 public 仓库。已登录账号为 feelingmine，使用该账号创建 [pagggle](https://github.com/feelingmine/pagggle)，默认分支 main。

本地工作区 `.git` 为只读限制，初始化返回 Operation not permitted。因此使用 GitHub Git Data API 上传经过检查的文件，生成一个当前已验证基线提交，再以非强制方式更新 main。没有伪造此前各阶段的历史提交；本地目录仍不是 Git checkout，不能声称本地 commit/push 成功。首次创建仓库的初始化 README 提交保留为父提交。

发布范围：根 README、AGENTS、.gitignore、空密钥 config.example.json，以及 backend/、frontend/、scripts/、docs/ 中的代码、测试和产品/工程文档。排除 config.json、data/ 全部运行与客户资料、虚拟环境、Python 缓存、包构建元数据和本地工具凭据目录。不添加未经指定的开源许可证。

基线验证：84 项自动化测试通过，2 条既有依赖弃用提示；Firecrawl 真实发现结果及未覆盖情况见分步开发记录。密钥检查在本地内存中匹配当前配置凭据与常见 token/私钥格式，仅记录命中文件路径，不输出凭据。

发布核验要求：逐文件计算 Git blob SHA，上传后比对远端完整树的路径、内容 SHA 和文件模式，再核对 main 指向本次提交且仓库为 public。具体提交 SHA 与逐文件清单保存在忽略的 data/verification 中，不将发布凭据或诊断响应加入仓库。
