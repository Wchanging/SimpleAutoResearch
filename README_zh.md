# SimpleAutoResearch

**面向任务的科研助手：调研、改码、分析与写作，交付可检查、可继续使用的成果。**

[English](README.md) · [快速开始](#快速开始) · [选择案例](#选择案例) · [文档](#文档) · [更新记录](CHANGELOG_zh.md)

在 CLI 引导中选择功能、说明任务，提供你已有的材料。SimpleAutoResearch 在保存的会话中组织必要工作，
交付可查看、修改、重建和继续使用的文件。

功能可以独立使用，也可以连接：**已有数据 → 分析绘图 → 报告 → 可选 ACM 导出**。
已有数据可以先分析和绘图，再复用结果写作；也可以加 `--with-report` 在同一会话生成报告。仅分析无需 API，写作需要模型。
修 bug 不需要先调研论文；使用已有结果写作，不需要重新跑实验。

> **公开预览。** 已提供引导式入口和限定范围的原生任务能力；通用自主复现准备与完整外部编码 Agent 接入尚未提供。请结合原文、实测记录和审计结果检查交付，不能将会话完成视为科研结论或论文质量认证。
>
> 许可证尚未确定，公开仓库访问不等于获得开源许可。

## 可以做什么？

| 功能 | 最小输入 | 交付与当前范围 |
| --- | --- | --- |
| 方向调研 | 问题、范围、模型 API；可选本地论文 | 来源、阅读笔记、比较与 Markdown 报告；深度取决于可获得材料 |
| 修改代码 | 项目、允许修改范围、验证命令、模型 API | 隔离副本中的改动、审查记录和实际验证；适合有限任务，不承诺任意无人值守工程 |
| 分析与绘图 | CSV/TSV 或 JSON 记录、字段、行和值的含义/单位 | 描述统计、显式配对差值或已有数值、可编辑柱状/箱线/折线/散点/矩阵热图 SVG、矢量 PDF、PNG 预览、重建输入；无需 API，误差解释依赖匹配观察的含义，不猜显著性 |
| 材料写作 | 笔记、草稿、JSON 程序记录或描述分析包；模型 API | 经审阅的报告或证据受限的论文体草稿、参考文献与图；不默认重跑实验或在线调研 |
| 有限复现 | 本地论文、已有项目/数据、已确认命令与评价协议 | 确认有源码依据的命令与输出文件契约，可选择任务虚拟环境，交付实测与报告草稿；不代表任意整篇论文复现 |
| 报告导出 | 保存的报告、Pandoc；按需 SVG 转换工具；编译另需 TeX | 单独用 `report-export` 交付可编辑 ACM acmart 演示工程、文献和图；编译与正文质量分开检查 |

前五项有 `start` 引导入口；导出使用已有报告，不是第六种启动选择。
研究改进仍属实验性，见后面的已准备案例。

### 为什么围绕这些功能做一个助手？

- **减少材料搬运。** 保存的分析包和报告可以供下一项任务使用，不只是孤立的聊天答案。
- **查看实际工作。** 计划、原文片段、补丁、测量、用量与被拒修订都有文件记录。
- **接着保存的工作继续。** 可复用兼容的已完成结果与支持的写作检查点，保留原用量。
- **保留控制。** 可以选择关键确认或授权范围内自主执行，关键输入与权限缺失仍需参与。

这些是产品希望提供的工作流收益，不是已证明优于通用编码 Agent。
跨任务质量与陌生用户体验仍在验收中。

写作围绕任务问题与已有证据组织文章，支持原文补读、审阅和保存修订。
分析包保留数据与可编辑图，可直接复用于写作；没有更强依据时只作描述性分析。
高级选择见[材料写作](docs/CONFIG_REFERENCE_zh.md#已有材料写作)和
[分析配置](docs/CONFIG_REFERENCE_zh.md)。

项目未准备好时，`project-info` 可保存只读准备笔记；复现对话可提出有源码依据的命令供确认。
可选任务 venv 安装依赖/项目时可能执行构建代码、联网，不是沙箱；默认仍使用当前环境，
不自动下载数据。命令、输入及准备边界见[引导设置](docs/CONFIG_REFERENCE_zh.md#引导设置)。

## 快速开始

需要 **Python 3.12+**、Git 和 [uv](https://docs.astral.sh/uv/)。

### 1. 安装

当前预览位于 `feat/v2.9-task-driven-research`，不是默认分支：

```bash
git clone --branch feat/v2.9-task-driven-research https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
uv sync
```

### 2. 无 API 体验

使用内置演示数据绘图，不需要密钥、GPU 或训练环境：

```bash
uv run simple-ar research-session --config examples/data-curves/research.toml
```

查看终端打印的分析包与 SVG 路径，产物位于 `runs/data-curves/`。
这是已有演示坐标的可重建绘图，不是实验或 benchmark 成绩。
具体范围见[案例说明](examples/data-curves/README.md)。

### 3. 为调研、改码与写作配置模型

**已有 `.env` 时不要覆盖**。首次使用可执行 `cp .env.example .env`；
PowerShell 使用 `Copy-Item .env.example .env`。填写服务商实际提供的值：

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-provider.example/v1
SIMPLE_AR_MODEL=your_model_id
SIMPLE_AR_LLM_API=chat
SIMPLE_AR_LLM_STREAM=false
```

接口格式须与服务商兼容，也支持 `responses`。流式返回可选，仅在服务商可靠支持时启用。不要提交密钥。
`.env` 只放全局模型与传输设置；项目路径、解释器、数据和任务限制放在对应任务配置。
[高级配置](docs/CONFIG_REFERENCE_zh.md)按需使用。

### 4. 启动自己的任务

```bash
uv run simple-ar start
```

编号菜单说明各功能的用途与准备要求；输入编号，或选择
`survey`、`bug_fix`、`reproduction`、`writing`、`data_analysis`。
结构化引导收集相关输入，在 `runs/assistant/` 保存普通 TOML，再进入共享会话；
不需要提前手写任务文件。`--prepare-only` 只保存配置，不执行或调用模型。
改码可加 `--project-python PATH` 选择已有项目解释器；省略使用当前环境，不创建环境或安装依赖。
也可以选择自然语言澄清：

```bash
uv run simple-ar start --chat
```

说明目标、回答影响结果的问题，确认功能与语义设置后，仍保存同一普通任务配置。
改码对话可根据已查看的项目说明建议测试命令，由用户确认命令与修改范围后进入普通执行；
明确的 `--validate` 不被替换，设置阶段不运行项目。回复和指定材料预览会发送给已配置模型，
因此澄清本身会产生 API 用量。`--resume-setup PATH` 恢复保存的对话与计费记录，
不是执行恢复或通用编程聊天。要完全不调用模型，请用结构化引导加 `--prepare-only`。

也可以直接运行模型驱动的综述案例：

```bash
uv run simple-ar research-session --config examples/survey/research.toml
```

该案例利用可获得的摘要和元数据调研小内存持续学习，**不下载全文、不训练模型**。
修改 `[task].goal` 可换问题；本地论文或尽力获取全文的设置见使用与配置文档。

> **费用：** 模型调用实际计费；累计请求/token 上限可选，未设置时不限。
> 进程时间、资源与科研轮次是不同限制。不承诺固定完成时间。

## 选择案例

一个目录对应一个案例的输入、配置和说明；运行产物进入 `runs/`。
直接使用版本库中的配置，不必先复制到 `runs/`。

| 案例 | 用途 | 准备 |
| --- | --- | --- |
| [数据曲线](examples/data-curves/README.md) | 已有坐标的折线/散点图 | 无 API、无训练；内置演示数据 |
| [描述分析](examples/data-analysis/README.md) | 表格、描述性柱状图与重建 | 无 API；明确数据与行语义 |
| [配对观察](examples/data-paired/README_zh.md) | 匹配差值、缺失对与标准误图 | 无 API；人工演示，不是 benchmark |
| [方向调研](examples/survey/README.md) | 无训练的调研报告 | 模型 API 与网络 |
| [Conformal 有限复现](examples/conformal_reproduction/README.md) | 限定范围、适配构造的数值复现 | 按案例准备环境与协议；不是整篇复现或 benchmark 验收 |
| [ICML 2025 RCP 子集](examples/rcp_reproduction/README.md) | 作者代码辅助的固定真实数据比较 | 先准备固定代码、数据、论文与科学Python；两方法、十配对种子，不是整篇复现，正文质量另验 |
| [多文件代码审查](examples/code_task_medium_review/README.md) | 受限修改与验证 | 生成改动需模型 API；项目使用 Python 标准库 |
| [Digits MLP](examples/code_task_digits_mlp/README.md) | 小型模型代码任务 | NumPy/scikit-learn；`uv sync --extra examples`，运行保留 extra |
| [持续学习](examples/continual_learning/README.md) | 实验性研究改进 | 已准备 Mammoth/CIFAR-100、固定切分、环境与 GPU 预算 |

两个代码案例使用独立 `code-task`，不是完整科研循环。
[TabM](examples/tabm_research/README.md)是已准备服务器上的诊断案例，不是新安装的快速体验。
更多边界见[案例索引](examples/README.md)。

## 实际交付什么？

| 交付 | 查看哪些文件 | 如何接着使用 |
| --- | --- | --- |
| 数据分析 | `analysis.json`、`analysis.md`、输入副本、可编辑 SVG、矢量 PDF 和 PNG 预览 | 重建分析包，或将 JSON 交给写作 |
| 报告 | `report.md`、`report_body.md`、`references.bib` 与另行打印的审计 | 对照原文检查主张，导出该报告 attempt 目录 |
| ACM 工程 | `main.tex`、`body.tex`、文献、图与 `export.json` | 编辑/搬迁；请求编译后查看 `build.log` |
| 代码/测量 | 修改后工作区、实际验证/运行日志、保存的结果 | 核对真实改动与评价条件，不只看总结 |

会话/attempt 的准确路径由终端打印。
保留整个分析目录，搬迁后仍能找到复制的数据。

例如，将完成的分析包接到写作，不重跑原任务：

```bash
uv run simple-ar start --kind writing --goal "解释这些结果与局限" --material "PATH_TO_ANALYSIS/analysis.json"
```

将 `PATH_TO_ANALYSIS` 换成打印的分析目录；写作需要模型。需要时单独导出该报告：

```bash
uv run simple-ar report-export --report-dir "PATH_TO_REPORT_ATTEMPT" --output runs/acm-draft
```

替换报告路径，并使用尚不存在的输出目录。导出需要 Pandoc，SVG 转换需要 `rsvg-convert`；
安装 pdfLaTeX、BibTeX、acmart 后才能加 `--compile`。未编译工程不是已验证 PDF 或会议投稿。
详见[导出说明](docs/CLI_REFERENCE_zh.md#simple-ar-report-export)。

## 使用自己的材料

先选择任务与交付：

- **调研：** 问题、范围、参考论文、是否允许在线检索。
- **代码：** 仓库、问题、允许文件与实际验证命令。
- **数据：** 表格、字段、每行/每个值的含义与单位。
- **写作：** 来源材料、报告或论文体草稿、可选参考论文。
- **复现：** 具体论文结论、准备好的执行条件与固定评价。

引导会保存可编辑任务配置，项目路径、数据、解释器与任务限制放在其中，不放入 `.env`。
完整操作示例见[使用指南](docs/USAGE_zh.md)，专家字段见[配置参考](docs/CONFIG_REFERENCE_zh.md)。

## 检查与续跑

终端显示会话目录、交付路径与当前行动。报告应与实测记录、来源证据一起看。

不改变输入的模型会话可使用保存的路径续跑：

```bash
uv run simple-ar research-session --session-root "SESSION_PATH" --model env
```

不要重新运行 `start` 来续跑。无模型的数据案例省略 `--model env`；
没有 `--session-root` 会新建会话。续跑不会重置耗尽的预算，也不表示断线后后台继续。
中断恢复前先确认原工作进程已停止；详见[CLI 参考](docs/CLI_REFERENCE_zh.md)。

新 CLI 会话默认 `checkpoints`；`--interaction assisted`、`checkpoints`、
`autonomous` 控制参与方式，案例可声明不同策略。
任何模式都不能替代缺失事实、资产或权限。

## 框架如何工作

```text
目标 + 材料 + 约束
        ↓
   选择必要工作 ←────────────────┐
        ↓                       │
 调研 / 改码 / 数据 / 执行 / 写作 │
        ↓                       │
 保存证据与产物 ──────── 重新判断
                                ↓
                           交付或请求输入
```

一套共享会话管理 attempt、用量、产物与恢复；领域模块负责自身证据和执行。
技术修复、科学修订与正文修订不是一种通用重试。

## 当前边界

- **阅读与写作：** 模型仍可能误读材料、遗漏条件或产生不完整引用。审阅和审计帮助发现问题，不保证全文事实正确或达到投稿质量。
- **数据与图表：** 支持描述性柱状图、箱线图、数值坐标折线/散点图和矩阵热图；不提供通用统计推断或自由科研插画。箱线须线表示实际 min–max，不是置信区间。
- **执行：** 限定修改和隔离副本不是操作系统沙箱。需准备目标项目的依赖与数据，避免不熟悉的代码接触敏感文件。
- **范围：** 外部 Agent 完整接入与通用自主复现准备尚未提供；不宣称取得正式 PaperBench 或 ScienceAgentBench 成绩。

各任务的执行与证据边界见[工作流说明](docs/WORKFLOWS_zh.md)。

## 文档

| 文档 | 用途 |
| --- | --- |
| [使用指南](docs/USAGE_zh.md) | 选择功能、运行、查看产物与排错 |
| [配置参考](docs/CONFIG_REFERENCE_zh.md) | 全局连接、任务输入和可选专家配置 |
| [CLI 参考](docs/CLI_REFERENCE_zh.md) | 命令、续跑与显式修订 |
| [工作流与产物](docs/WORKFLOWS_zh.md) | 证据、执行与保存结果的边界 |
| [开发指南](docs/DEVELOPMENT_zh.md) | 模块职责与工程契约 |
| [更新记录](CHANGELOG_zh.md) | 已实现变化与迁移提示 |

## 参与贡献与致谢

欢迎问题反馈与可复现案例；许可证确定前，代码贡献请先沟通再提 PR。
附命令、相关配置与诊断信息时，**移除凭据和私有数据**。

工作流与呈现参考包括 [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)、
[OpenResearch](https://github.com/alphaXiv/OpenResearch) 与
[ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep)。
项目保留自身紧凑、基于文件的运行底座与受限原生功能。报告、案例与使用体验同样重要。
