# quantagent

A 股策略与因子研究项目，沿用原 Python 包名 `quanta_agents`。

当前研究主线位于 `src/quanta_agents/factor_lab_a/`：程序负责因子枚举、改造、交叉、评价、组合与重放，模型参与诊断和复盘。仓库也保留旧版多智能体与元研究框架、测试和研究记录，便于追溯演进。

## 阅读入口

- [Choice 模拟交易控制台：安装、一键启动与队友策略接入](tools/choice_trader/README.md)
- [给 Codex 的控制台安装与接手流程](tools/choice_trader/CODEX_SETUP.md)

- [因子目录：日频396、研究597、完整定义与来源](docs/factors/README.md)
- [项目主记录与当前状态](master.md)
- [A 层操作说明](docs/FACTOR_LAB_A.md)
- [比赛日更流程](docs/COMPETITION_OPS.md)
- [框架复盘与迭代记录](docs/research/framework_review_20260913/)
- [原版 README 与旧框架运行说明](README_LEGACY.md)
- [仓库范围、数据依赖与迁移说明](docs/REPOSITORY_CONTENTS.md)
- [早期策略与实验源码](legacy_examples/)

## 环境与数据

依赖声明保留在 `pyproject.toml` 和 `requirements-meta.txt`；A 层核心使用的额外库列在 `requirements-alayer.txt`。环境配置从 `.env.example` 复制为本地 `.env` 后自行填写，不要提交真实凭证。

本仓库不包含行情数据库、F 盘研究面板、模型缓存、逐笔回测产物或实时目标持仓。部分程序仍保留原机器的数据路径，也会加载仓库外的 Alpha101/191 和因子日历模块。使用前需按迁移说明准备数据、外部因子库和路径；克隆仓库不代表已具备完整运行环境。

## 历史与研究边界

这是从本地工作目录筛选形成的源码快照，新建了独立 Git 历史；初次发布时为私有仓库，截至2026-09-18已公开。旧仓库、密钥文件和完整原始实验产物仍在本地保留。

各阶段报告保留原有日期、口径和失败证据；历史报告中的“当前”“下一轮”只对应当时阶段。代码上传和静态检查不构成研究结果重算、独立样本外验证或实盘盈利证明。
