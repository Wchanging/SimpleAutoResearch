# CLI 参考

[English version](CLI_REFERENCE.md)

本文用于查命令语法与操作效果。首次使用看[使用指南](USAGE_zh.md)，TOML 字段看[配置参考](CONFIG_REFERENCE_zh.md)。`simple-ar --help` 及各命令 `--help` 显示当前安装版本的选项。

## 成果复用与概念图

独立方法图/架构图使用 `simple-ar start --kind figure --goal "绘制观测→比较→证据的关系"`。
无需制造假数据，可选 `--data-file` 提供真实表格；复用脚本项目与 CodeTask，交付 PNG、
可编辑矢量 SVG、设计记录和解释。结构检查不代表语义或视觉质量通过；此入口暂不直接解释论文材料。

引导代码任务默认允许一轮技术修复。未指定进程限额时，`research-session` 为首次验证及已配置
的修复复验预留额度；用户明确的限额（包括零）仍优先。修复额度不授权安装、改变科研协议
或无限重试。

`simple-ar start --from-session SESSION --reuse code_project --goal "调整布局"`
在新隔离任务中继续已验证的代码/绘图项目，保留登记的编辑范围、保护文件、验证命令和补丁策略，
不改原交付。此选择不能混用写作材料选择，也不能同时覆盖项目、编辑范围或验证命令。

`simple-ar results SESSION` 列出当前会话登记的可复用成果。
`simple-ar start --from-session SESSION --reuse data_analysis --goal "解释性能与开销的权衡"`
可直接用分析包写作；`--reuse report` 或 `summary` 复用相应成果，可重复选择。
`--reuse code_analysis` 复用已验证代码分析项目。新生成的登记交付包包含脚本、输入数据、
解释、JSON 结果和 PNG/矢量导出，可选正文图并保留完整链接附件；复制脚本输出不代表
本次独立数值复算。未登记交付包的旧会话仍只复用解释与 JSON。
这是新写作任务，不是中断恢复；原会话不修改、不重跑。报告携带已登记原文包、实验依据与引用身份，
保存的段落可继续读取，不重新下载；
引用元数据不等于原文或本次重新核验，正文仍按草稿处理；分析包沿用源数据复算。

需要重组论证或章节结构时，用 `--reuse report --goal "重组论证与章节结构"` 新建自适应
写作任务，复用登记证据，不改原任务已接受的大纲；普通修订保留原大纲。
生成分析/绘图项目以 `analysis.py` 为入口，辅助模块和可编辑布局可放入 `src/`，
该目录随脚本和数据进入可搬迁交付包。

`simple-ar image --config models.toml --prompt "方法概览" --output figures/v1`
使用命名目录的 `image` 路由；可用 `--model profile:NAME` 选择连接。
编辑时加 `--input figures/v1/image.png`，并选新输出目录，例如 `figures/v2`。
原图不覆盖，版本保存请求和单次调用账本；相同已完成请求恢复不再次调用。
失败或中断不自动重发。通用 `openai_images` 要求提供商返回 base64 图片，拒绝 URL-only 结果。
显式 `cctq_images_async` 在已有 task ID 时恢复同任务轮询，认证下载配置同 origin 的
`files/0`，不重复 POST；没有保存 ID 的请求不自动重提。该适配要求提供商支持相应异步协议，
不自动切换为其他 API 或模型。
已记录完成的任务直接恢复文件下载；可调整恢复的超时和 HTTP 协议，不改变生成内容、原请求记录或重新提交。
编辑连接须声明 `image_edit`；位图概念图不是科学测量或分层矢量源文件。

`simple-ar image-review --config models.toml --input figures/v1/image.png --goal "检查标签、箭头和多余文字" --output figures/review-v1`
通过独立 `vision` 路由检查实际图片；可用 `--model profile:NAME` 选择其他视觉连接，
重复 `--input` 提供最多四张图。保存源图副本、原始响应、`review.json` 和 `feedback.md`。
相同完成请求恢复不调用 API；已有原始响应可离线重新解析。输入改变须使用新输出版本，
未保存响应的中断请求不盲目重发。反馈可用于原代码项目或新 `image --input` 编辑，
审阅命令本身不改图，也不证明科学正确性。

代码分析/自定义绘图可用：
`simple-ar start --kind data_analysis --scripted --data-file measurements.csv --goal "比较性能开销并做三面板图"`。
该路径需要模型和普通执行确认，生成小项目后复用 CodeTask，数据和交付检查器受保护。
交付脚本、计算 JSON 和解释；无图分析不要求占位图，outputs/中的多张命名图与表格均保留。
同名 PNG/SVG/PDF 按同图导出登记；不安装依赖。
文件检查不等于统计正确性，工作区隔离不是 OS/网络沙箱，宜在合适环境运行。
内部沿用 `bug_fix` 执行路径，不新增运行时；确定性表格路径保持不变。
对话可为超出预设的自定义统计或组合图提议此路径，经确认后进入；无需用户手选
`--scripted` 或预填 `value_column`。数据语义不明仍需澄清，原用户回复与单位保留在
`task.md`。该路径已含解释，不与 `--with-report`、`--material`、`--project` 混用；
入口路由已实现不等于真实图形交付验收成功。
新建 scripted 分析 scaffold 使用既有 `large` 补丁档（新文本块最多16000字符），执行前明示；
普通项目修复额度不变，完整新分析脚本不再套用 normal 的4000字符块上限，不据此宣称真实图已成功。

## 引导入口

已验证的复现项目可用 `start --kind reproduction --from-session SESSION --reuse code_project
--document PAPER --goal "检查就绪并重复有限测量"` 生成隔离任务，继承单命令协议、输出指标选择器、
编辑范围、检查器和解释器，不重新创建已有适配器或安装依赖。执行前确认显示的范围。
配对或扩展协议继续用原会话恢复；要改变测量条件，应另建任务。

`simple-ar models [--config PATH] [--profile NAME --purpose text|code|vision|image]`
本地检查命名连接和凭据是否配置，不调用 API、不显示密钥。
启用与路由见[模型配置](CONFIG_REFERENCE_zh.md#命名模型连接)。

```bash
uv run simple-ar start
uv run simple-ar start --chat
uv run simple-ar start --kind writing --material notes.md --model env
```

`start` 准备普通任务，再交给 `research-session` 执行。

复现对话在读取项目源码与命令依据后，可提出明确的新 Python 适配文件范围和独立验证命令。
用户确认后写入原 `allow` / `validate` 配置，不覆盖已明确给出的范围或命令；
提案本身不执行代码，也不代表适配器已经实现。
可明确选择任务 venv，在同一隔离工作区先准备授权依赖，再由同一解释器改码、独立验证
和测量；安装失败即停止。基础解释器只用于创建任务环境，不额外指定 `check-argv`。
授权准备时才在隔离工作区初始化明确的新文件，再建立索引；任务输出须放在作者项目外。
没有创建声明的普通 `--allow` 路径仍须已经存在。

在线调研在模型阅读发现明确缺口后，可在自动扩展开启时追加一轮搜索、取得与阅读，
沿用原有限额和已保存证据。`--fulltext` 仍决定远端原文权限；找到摘要不等于取得原文支持。

| 选项 | 适用范围 | 用途 |
| --- | --- | --- |
| `--kind`、`--goal`、`--output-root` | 全部 | 功能、任务、输出位置 |
| `--model`、`--interaction` | 模型任务 | 模型连接与参与策略 |
| `--prepare-only`、`--yes` | 结构化设置 | 仅保存、不执行/API；或确认已提供选择 |
| `--chat`、`--resume-setup PATH` | 模型辅助设置 | 澄清任务或恢复设置草稿 |
| `--sources materials\|search`、`--document PATH`、`--fulltext`、`--max-cited-sources N` | 调研 | 来源访问、可重复论文、全文与引用上限 |
| `--material PATH`、`--template` | 写作 | 可重复笔记/结果与报告形式 |
| `--project`、`--validate`、`--allow PATTERN`、`--project-python PATH` | 改码 / 显式复现 adapter | 项目、独立验证、可重复修改范围、已有解释器 |
| `--data-path PATH` | 改码/复现 | 可重复数据文件/目录，不是表格分析输入 |
| `--hypothesis`、`--dataset`、`--expected-outcome`、`--metric` | 复现 | 结论、条件、输出指标 |
| `--cwd`、`--timeout-sec`、`--output-files JSON` | 复现 | 目录、超时、登记输出文件 |
| `--metric-sources JSON` | 复现 | 从登记文件按 JSON 路径或 CSV/TSV 唯一行取指标，不隐式汇总 |
| `--environment current\|venv`、`--requirements PATH`、`--install-project` | 复现 | 明确依赖准备 |
| `--check-argv JSON` | 复现 | 明确的测量前短检查；不猜测命令，检查指标不进入测量结果 |
| `--command ARGV...` | 复现 | 实际命令，必须最后提供 |
| `--with-report` | 数据分析 | 附加写作，需要模型 |

作者输出参数可用字面量 `{output_dir}` 绑定本次运行目录，不经 shell 展开。
文件指标选择格式见[配置参考](CONFIG_REFERENCE_zh.md)。

功能为 `survey`、`bug_fix`、`reproduction`、`writing`、`data_analysis`、`figure`。结构化设置在执行前不调用模型；chat 设置本身需模型和终端，不能与 `--prepare-only` 同用。

改码对话可只读查看项目文本并提出带原文依据的验证 argv；用户确认后序列化原 CodeTask
验证命令，保留明确的 `--validate`。设置阶段不运行命令或安装依赖，普通引导仍确认修改范围与执行。

改码默认当前解释器；`--project-python` 保留已有 venv 入口，不创建或安装环境。复现也可用它选择正式命令与短检的解释器；明确选择 `--environment venv` 时，它指定新任务环境的基础解释器。对话可经确认采用用户原话提供的已有解释器路径，不从源码说明中自行选择。

复现默认仍走固定命令路径。已有 Python 项目可显式组合 `--project`、可重复的
`--allow PATTERN` 和 `--validate "…"`（已调查的项目检查命令），授权限定范围的结果
adapter 或入口衔接。验证命令必须不同于最后提供的正式测量 `--command`；验证输出
不是科学测量。CodeTask 仅在隔离 worktree/copy 的允许范围内修改，保护测试、秘密文件
和已登记的项目内数据；验证通过后才在该项目中运行正式命令。若给 `--cwd`，须与
`--project` 相同。可使用 current Python 或已有 `--project-python` 解释器；选择
`--environment venv` 时，可在同一隔离工作区先安装明确授权的依赖/项目，再由同一解释器
验证和测量，而非在基础解释器中测量；不能添加额外 `--check-argv`。这不授权改变方法、划分或评价
条件，工作区隔离也不是 OS 沙箱；不代表任意论文自主准备或真实论文复现已验收。

对话可根据已读论文或项目材料的引文提议取得公开项目/数据/补充材料，每项须独立确认。
项目支持直接 ZIP 或 GitHub 仓库默认分支快照，不是固定 commit；数据保留单个下载文件，
不自动展开。最多3项资产，默认每项下载20 MiB，项目 ZIP 展开最多80 MiB、5000项。
对话启动前可用 `--asset-max-mb N` 显式选择项目/数据下载容量，ZIP展开上限为 `4*N` MiB；
确认时展示容量，获取记录保存实际采用的上限，恢复不重新下载。ZIP格式按实际归档检查，
不要求URL以`.zip`结尾。这个选项本身不授权下载、
安装或执行；补充文档仍使用独立的文档容量限制。
仅接受不带 query 的公开 HTTPS URL；取得不授权执行或安装依赖，也不是安全沙箱或完整防 SSRF。
补充材料可为官方说明页或论文，复用文档预览和按问题补读；单次公开GET不跟随重定向，
PDF许可独立确认，同时计入资产/文档限额，保留出处与下载账本，不作为执行命令授权。
取得路径已实现，但完整真实复现验收仍未通过。

论文链接可用 `start --kind reproduction --chat --document https://… --goal "…"`。
先确认取得，再向模型提供带来源的摘录；PDF下载/原文件保留另需许可，或显式 `--fulltext`。
初次预览最多8000字符、PDF前3页，不代表读懂全文。需要后文方法或说明时，对话可对已提供的
文档或材料提出按问题只读补查；扩大本地解析须确认，最多40页PDF、每文件20 MiB，只发送
有界命中片段。恢复复用保存的文档包与取得记录，不重复解析或取得；
普通 `--prepare-only` 不下载链接。仓库与执行建议仍须已读原文依据和分别确认。

### 已有数据分析（无需 API）

使用 `start --kind data_analysis`；需要正文再加 `--with-report --model env`。数据参数与 `research-session` 共用：

| 参数组 | 用途 |
| --- | --- |
| `--data-file` | 输入表格 |
| `--group-column`、`--value-column` | 分组和可重复值列 |
| `--observation-unit`、`--value-unit`、`--data-attribution` | 声明语义 |
| `--data-mode observations\|values` | 默认聚合逐行观测，或保留已有数值 |
| `--data-plot bar\|box\|heatmap\|line\|scatter` | 图形类型，默认 `bar` |
| `--data-missing reject\|omit` | 默认拒绝缺失值，或明确选择遗漏策略 |
| `--paired-baseline COLUMN` | 与指定基线列计算同一行的配对差值 |
| `--x-column`、`--x-unit`、`--data-association none\|pearson` | 数值坐标与可选描述性相关，默认 `none` |
| `--series-layout separate\|shared`、`--figure-width column\|wide` | 坐标轴默认 `separate`，图宽默认 `wide` |
| `--data-max-mb`、`--data-max-points`、`--data-max-figures` | 输入/图形上限，默认 `20` MiB、`10000` 点和 `100` 图 |

附加报告的上下文在 `start` 中使用 `--material`、`--document`，
在 `research-session` 中使用 `--material`、`--local-document`；不改变数值变换。
单独分析忽略模型设置。选项组合要求见[数据字段](CONFIG_REFERENCE_zh.md#已有数据的描述分析)。

## 项目准备

```bash
uv run simple-ar project-info --project ./project --data-path ./data \
  --output runs/project-preparation-NEW
```

保存 `preparation.json` 和 `preparation.md`，含入口候选、依赖声明、摘录和指定数据位置。不执行、安装或下载项目代码/数据；静态依赖观察针对检查解释器，不代表目标环境已解析完成。

使用未占用目录；相对数据路径从项目根解析。说明可复用于写作或复现设置。

## 命令总览

| 命令 | 用途 |
| --- | --- |
| `simple-ar start` | 引导设置调研/代码修复/固定协议复现/材料写作/描述数据分析，保存可编辑配置并进入同一 research-session。 |
| `simple-ar research-session` | 有界任务驱动 session 的正式入口；accepted plan 选择适用的研究与执行步骤。 |
| `simple-ar research-session-continue` | 重试 canonical 显式实验中的技术失败。 |
| `simple-ar research-session-migrate` | 从只读的 `session_manifest.v1` 创建 canonical 后继 session。 |
| `simple-ar research-report` | 从已完成的 research session 生成并审查报告。 |
| `simple-ar report-export` | 将已组装报告转换为新的可编辑 ACM 工程，可选本地 TeX 编译。 |
| `simple-ar research-brief` | 分段/开发接口：从主题或本地文献构建有证据支持的 research brief。 |
| `simple-ar status` | 查看 research run 或 code-task run 状态。 |
| `simple-ar tools ...` | 导出 tool schema、调用 run-local tool，或通过 MCP stdio 暴露只读 tools。 |
| `simple-ar inspect` | 为某次 run 构建本地 artifact index。 |
| `simple-ar search-artifacts` | 搜索已经索引的 run artifacts。 |
| `simple-ar clean` | 预览并清理某次 run 的可重建缓存。 |
| `simple-ar code-task ...` | 在隔离可编辑 workspace 中处理已有代码项目。 |

## Research 命令

### 报告控制参数

两个报告入口使用同一套设置，选项前缀不同：

| `research-session` | `research-report` | 用途 |
| --- | --- | --- |
| `--report-document-review` / `--no-report-document-review` | `--document-review` / `--no-document-review` | 开启或关闭整稿审阅 |
| `--report-review-scope section\|document` | `--review-scope section\|document` | 逐节审阅或正文完成后整稿审阅 |
| `--report-draft-scope section\|document\|auto` | `--draft-scope section\|document\|auto` | 分节、联合，或仅在完整定长计划不超过 2,000 词时联合起草 |
| `--report-outline-strategy auto\|template\|adaptive` | `--outline-strategy auto\|template\|adaptive` | 模板或证据驱动的章节规划 |
| `--report-data-tables linked\|full` | `--data-tables linked\|full` | 链接数据记录，或同时附摘要表 |
| `--max-document-review-prompt-chars N` | `--max-document-review-prompt-chars N` | 可选的完整审阅请求字符上限 |

省略选项沿用配置或已保存设置。基础默认是分节起草/审阅、关闭整稿审阅、`auto` 规划、
`linked` 数据表及不额外限制完整审阅请求（`0`）；引导入口可能保存不同选择。
联合起草要求开启整稿审阅、选择整稿审阅范围和完整来源策略；依赖关系及持久化修订上限
见[配置参考](CONFIG_REFERENCE_zh.md)。

### 固定协议复现

固定协议论文结论检查使用 `research-session --config examples/conformal_reproduction/research.toml`，
先按案例 README 准备论文。`--task-kind reproduction` 要求本地材料和显式固定协议，
不代表任意论文的自动环境探索/代码实现。

### `simple-ar report-export`

```bash
uv run simple-ar report-export --report-dir 报告attempt目录 --output runs/acm-draft --compile
```

输入目录须包含保留引用键的 `report_body.md`；有引用时还需 `references.bib`。
Pandoc 转换已有正文，不调用模型或重跑实验。目录内的图复制到工程，SVG 转换需
`rsvg-convert`；不抓取远程图片。正文使用 PNG 时仍保留已登记的矢量导出。
可用 `--title` 指定面向读者的标题。
每次选新的输出目录，保留已有导出及人工编辑。

`source.md` 与 TeX 使用包内图路径，可搬迁整个目录；可编辑 SVG 与转换后的图一同保留。
指向外部本地证据的链接改为标签，原目标记录在 `export.json`；不会自动复制或重分发
数据集和来源产物。正文事实审计仍需单独查看。

输出为单栏 `acmart` 演示工程，含 `main.tex`、`body.tex`、文献、图和 `export.json`。
若已有图表清单，导出将登记 caption 绑定到对应图，仅移除紧邻且完全相同的重复图注段；
没有清单则保留输入图注，不自行编造内容。
生成的图优先就近放置（`htbp`），仍允许正常 LaTeX 浮动，不强制原地；编译通过不等于视觉验收。
它不替用户选择具体会议投稿选项，也不认证科研结论。需安装 Pandoc；加 `--compile`
还需 `pdflatex`、`bibtex` 和 acmart 依赖。缺编译器或编译失败时返回非零状态，保留
源码和诊断；实际编译写入 `build.log`。当前使用 pdfLaTeX，中文等非拉丁文字需自行
调整可编辑工程的引擎和字体。


### `simple-ar research-brief`（分段/开发接口）

**一句话说明**：从主题或本地 Markdown/TXT 文献构建一个有证据支持的 research brief。

**语法用法**：

```bash
uv run simple-ar research-brief \
  --topic "reliable agents" \
  --local-document tests/fixtures/research/reliable_agents.md \
  --output-root runs/research-brief
```

命令会创建带时间戳的 session，并把 plan、search、document ingest、read 和 synthesize
分别记录在独立 attempt 中。它不会静默 retry 或覆盖 attempt；`--query`、`--provider`、
`--max-results`、`--max-chunks` 和 `--idea-limit` 是这条路径保留的少量控制项。
后续 session 可以传入同一个可选的 `--cache-dir` 来复用已下载的全文；省略时缓存仍保留在当前 session 内。

省略 `--model` 时，这条入口使用可复现的 deterministic planning 和 evidence derivation；
如果希望真实调用共享 LLM transport 完成研究问题/查询规划和证据综合，可以显式传入模型：

```bash
uv run simple-ar research-brief \
  --topic "reliable agents" \
  --local-document tests/fixtures/research/reliable_agents.md \
  --model "$SIMPLE_AR_MODEL"
```

LLM 模式仍使用正常的 `.env` provider 配置。缺少 key、模型请求失败或返回无效结果时，
对应 attempt 会明确失败，不会偷偷改用 deterministic 正文。

### 已退出的 `simple-ar research-experiment`

独立 design→experiment→analysis 创建器已删除；研究任务使用 `research-session`。
旧 `--synthesis-file` 参数不作隐式兼容。领域能力仍可在库级组合，历史产物保持可读。

### `simple-ar research-session`（任务驱动的 canonical 入口）

LLM 任务计划校验失败时最多自动纠正一次，不静默改用固定计划。
返回提案与校验错误保存在该次 `attempts/plan-*/task_plan_proposals.json`；仍无有效计划时暂停，不执行后续动作。

可加 `--session-root PATH` 继续已保存的 accepted plan。未提供的新目标/outputs 保持不变；
显式提供新目标、outputs、本地文献或执行配置会修订当前 session。attempt 历史保留；只有
命令、结果 schema、协议、准备 lineage 和保护资产仍匹配的测量才复用，其余依赖步骤重新规划。
新增文献本身不会重跑有效实验；task kind 改变仍须新建 session。详见配置参考的续接说明。

进程被杀且还没保存结果时，确认旧进程已停止，再用
`--session-root PATH --recover-interrupted --model env` 恢复。旧 attempt 计入原预算，
保留此前证据和有效章节检查点，重试当前步骤；配置/输入修改另一次续跑处理。
已有失败结果的普通 API 故障使用常规续跑即可。
恢复时未显式提供的报告选项沿用已存设置；对已请求报告的 session，显式报告改动只重建报告产物。
尚未请求报告的前缀请使用 `research-report` 添加交付物。与已存 session 不匹配、
且不能安全修订的搜索/摄取设置会在执行前拒绝；要应用这些设置请新建 session。
`[research].max_iterations` 限制有界后续轮次；`0` 表示首轮分析后停止。

#### 参与策略与待处理决定

新 CLI 会话默认使用 `checkpoints`；用 `--interaction assisted|checkpoints|autonomous`
选择策略，也可在 TOML 的 `[research] interaction = "checkpoints"` 设置。
旧会话没有该字段时继续保持旧行为。三种模式都不会代替缺失的事实、资产或权限；这类条件仍须补充，
不能用 `accept` 绕过。

模式变更与 `--reanalyze` 或单独刷新报告须分次续接，不能组合后静默忽略模式。
交付决定的正式答复可以同时包含报告设置和模式变更。

- `assisted`：在首次执行协议、自动选择的分析报告等关键节点确认；也会询问补测或候选修订等科研选择。
- `checkpoints`：确认首次实验协议、实质方向修订和自动交付取舍；同协议内的有界补测仍按既有授权执行。
- `autonomous`：在有效选项与剩余轮次内自动继续；没有充分依据或前置条件时暂停并保留证据。

暂停时 Rich 展示决定 ID、问题、依据、选项和续接命令。正式答复仍走同一 `research-session`：

```bash
simple-ar research-session --session-root runs/research-session/<session> --topic "原研究目标" \
  --decision-id ID_FROM_RICH --decision-response accept

simple-ar research-session --session-root runs/research-session/<session> --topic "原研究目标" \
  --decision-id ID_FROM_RICH --decision-response revise \
  --decision-guidance "补充事实或修订后的研究方向"
```

`--decision-response` 可取 `accept`、`reject` 或 `revise`。修改交付选择时，`revise` 必须同时提供报告配置，
例如 `--report-template analysis_report`。TOML 可在 `[continuation]` 提供同名的 `decision_id`、
`decision_response`、`decision_guidance`。答复产物记录 brief、执行或报告修订值；若答复已落盘但输入尚未保存时
进程中断，使用相同决定和修订重放会恢复原修订，不同修订值会被拒绝。输入持久化后的精确重放不会重复已完成的工作，
相关输入改变后旧决定不再适用。Rich 只读显示，不会等待终端 stdin。

额度续接仍使用 `research-session`。可通过 `[continuation]` TOML 或 CLI 提供新额度：
`--authorize-remaining DIMENSION=AMOUNT` 可重复，表示授权后后续调用的新剩余额度，不会估算或
消除未知历史用量；`--additional-attempts` 与 `--additional-no-progress` 只增加持久化上限，不清零
计数。所有授权都需要稳定的 `--authorization-id` 和 `--authorization-reason`；相同 ID/条款重放
幂等，条款变化须使用新 ID。不得直接编辑账本或 manifest。

已完成会话若只追加额度而不修改输入，将保持 `completed`，不执行任何研究或报告动作。
刷新报告时可先用上述入口授权，再运行 `research-report --refresh` 或原报告配置修订命令。
完整报告刷新通常需要 writer、组装、审计三个 attempt，失败重试另计；纯报告不要求增加进程预算。

```bash
simple-ar research-session --config research.toml --session-root runs/research-session/<session> \
  --local-document new-paper.md \
  --authorization-id review-20260924-1 --authorization-reason "审查后有界续接" \
  --additional-attempts 2 --additional-no-progress 1 \
  --authorize-remaining total_tokens=50000 --authorize-remaining llm_requests=8
```

在已暂停或完成的分析检查点，可单独使用 `--reanalyze`（不与额度授权或输入修订合并）：复用已有测量重新分析，
再按结果继续研究决策。历史产物和已消耗预算保留，不增加论文交付要求；
后续若接受候选修订，仍会消耗原预算执行新实验。不能用它覆盖尚待执行的科研后续计划。

结束时的产物表按 accepted plan 展示最新实现、候选测量和分析；早期修订引用仍单独保留，便于追溯候选 lineage。

可使用 `simple-ar research-session --config examples/survey/research.toml`。
显式 CLI 覆盖文件值；`--outputs summary report experiments` 按需选择交付。
预算覆盖项为 `--total-tokens`、`--llm-requests`、`--max-output-tokens`、
`--process-invocations`、`--process-wall-seconds`。见[配置参考](CONFIG_REFERENCE_zh.md)。

**一句话说明**：运行一个有界 session，由 accepted plan 选择适合任务与已有资产的步骤。
提供实验命令或 `--code-task-config` 时，计划可包含准备、实现、测量和分析；两者都省略时，
session 保持 literature-only，不会创建执行请求或启动进程。

模型可用时，同一个 application 会继续进入 `report -> report_audit`；`--no-report` 仅用于调试
或只检查前缀 handoff。之后调用 `research-report` 可以只补齐报告交付物，不重新检索、分析或
运行已有实验；`research-session-continue` 只用于明确的恢复或修订决定。

**语法用法**（`--command` 必须放在最后）：

```bash
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document tests/fixtures/research/reliable_agents.md \
  --cwd tests/fixtures/research \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.75')"
```

如果希望在同一个 session 中使用已有的 Code-Task 实现后端，可以省略 `--command`，
改为提供 Code-Task TOML 和模型：

```bash
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document tests/fixtures/research/reliable_agents.md \
  --code-task-config examples/code_task_medium_review/configs/code_task.toml \
  --model "$SIMPLE_AR_MODEL" \
  --output-root runs/research-session
```

TOML 仍然是 Code-Task 项目、benchmark、workspace、baseline 和执行设置的来源。
生成的 code-task 产物会保留在 session 的 preparation/implementation attempt 下，并规范化为
同一份 Analysis 消费的 canonical result；不会新增第二套代码生成器。

canonical application 会在隔离的 preparation workspace 中复用既有 Code-Task 实现和验证能力。
如果 proposal 需要 `large` budget，检查 proposal 后必须在 Code-Task TOML 中显式设置
`[execute].allow_large_edits = true`；否则 session 会保留产物，并在审批边界停止。

可选的 `--cache-dir` 会传给 document ingest。后续 session 会复用有效的全文缓存文件；默认值仍是
session-local，以保持旧命令兼容。

它复用分段接口的 attempt-local 产物，但这些接口不再构成第二条完整主线，并且主线不隐式
retry 或 repair。若还没有准备好的可执行实验，`research-brief` 只能作为提前准备 handoff
的开发工具；实验继续由正式研究会话管理。
使用 `--no-report` 创建的 session 之后可用窄的 `simple-ar research-report` 命令补齐报告；
它只重新打开 canonical application 的报告动作，复用已持久化的 synthesis、测量和 analysis。
如果希望一次显式调用完成前缀和报告，可以在最后的 `--command` 之前加入
`--model NAME --with-report`；`--report-reviewer`、`--max-review-iterations` 和
`--max-section-tokens` 会进入同一份 application 配置；最后一个参数设为 `0` 时不设置章节
单次调用的 provider 输出上限。
Writer 执行和检查点统一由正式应用调用 `report/writing.py`。历史报告输入投影只读保留，
原来的独立 report-session 执行 API 已退出。

省略 `--model` 时，planning、reading、synthesis、design 选择和 analysis 都保持
deterministic；传入 `--model NAME` 后，同一个共享 client 会用于 planning、有界 reading/
screening 与 paper notes、synthesis、在已有研究方向中进行选择以及结果分析。provider 失败会
保留为可见错误，不会静默转换成离线输出。

如果只想查看已经持久化的 capability session，而不重新运行任何阶段，可以继续使用已有的
status 命令：

```bash
uv run simple-ar status runs/research-session/<session>
```

当目录包含 canonical `session_manifest.json`（schema `session_manifest.v2`）时，status 会显示
application 状态、当前 attempt、有限预算、各类 attempt 计数和最后一次决策；不会读取或改写 capability
产物。旧的 `session_manifest.v1` 只作为只读兼容输入；旧 pipeline 和 Code-Task 目录继续使用原来的
status 行为。

如果开放的 session 没有 active attempt，status 还可能显示
`Handoff: ready_for_report` 或 `Continuation: explicit ...`。这只是持久化的下一步提示，
不表示后台仍有进程运行；下一步必须由调用方显式执行。

### `simple-ar research-session-continue`

**一句话说明**：在已有 session 中用调用方提供的修正命令，显式重试一次技术失败的实验。
对于 canonical v2，它复用文献、research design 和失败的父 attempt，不重新检索；成对实验、数据准备
和 CodeTask 失败由各自的有界边界处理。科学负结果是证据，不会被静默重跑；旧 v1 session 不再原地执行。

**用法**（`--command` 必须放在最后）：

```bash
uv run simple-ar research-session-continue \
  --session-root runs/research-session/<session> \
  --cwd tests/fixtures/research \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.90')"
```

canonical session 会创建动态命名的新 experiment attempt，并把失败的候选记录为父节点，随后重新执行确定性
analysis；原 attempt 和文献产物不会被覆盖。只允许重试 `failed`/`timed_out` 技术失败，默认沿用原 result
schema，metric 参数可显式补充或覆盖。若请求了报告，成功后会显示下一步 report action。
旧会话可显式迁移证据，但迁移不等于继续执行旧阶段计划。

### `simple-ar research-session-migrate`

**一句话说明**：从旧的 `session_manifest.v1` 创建新的 canonical session，
不改写旧目录。不会猜测历史预算剩余量，也不会把未知旧产物冒充为当前状态；只有显式指定且可读取的小型产物才会迁入。

**用法**：

```bash
uv run simple-ar research-session-migrate \
  --source-root runs/legacy/<session> \
  --destination-root runs/research-session/<successor> \
  --artifact search \
  --requested-output report
```

命令会输出新 session 路径、`parent_session`、迁移 artifact、导入/跳过数量，
以及 `Historical budget: unknown_not_imported`。目标目录必须为空，且不能位于旧 session 目录内。

### `simple-ar research-report`

**一句话说明**：继续一个分析结果已经可以进入报告阶段的 `research-session`。该命令复用现有的 Writer/Reviewer、装配器和审查实现，不会重新检索文献或重新运行实验。

```bash
uv run simple-ar research-report \
  --session-root runs/research-session/<session> \
  --model "$SIMPLE_AR_MODEL"
```

可用 `--max-section-tokens N` 显式设置 Writer/Reviewer 每个章节的输出上限；设为 `0`
表示不添加该单次调用上限，这也是报告运行时配置的默认值。会话级 token 预算仍然有效。

报告和审查会作为新的 attempt 写入原 session。canonical session 已有报告时再次调用是幂等读取，
不会重新运行 Writer。显式添加 `--refresh` 会复用已保留的 analysis，仅创建新的报告/审查 attempts，
保留历史稿件，不重复测量或分析；模型会话使用模型解释假设，数值和执行状态仍由确定性分析负责。
刷新仍使用会话剩余预算。只有需要明确做 writer-only 对照时才使用 `--reviewer disabled`；最终 audit
仍会运行。历史 session 可读取，但不再由第二套报告执行器续写；损坏的正式 session 会明确失败，
不会回退到另一条流程。
未提供的 template/reviewer/limit 选项沿用 session 已存设置；显式选项只更新报告配置并重建报告产物。
持久化 attempt/no-progress 上限耗尽时，请通过 `research-session` 做显式授权续接；报告路径不绕过上限。

### 已退出的分段 CodeTask 命令

research-code-task 及其独立 session 执行器已退出。研究任务使用
research-session --code-task-config，纯编码任务使用独立 code-task；历史分段结果仍可读取。


### `simple-ar status`

**一句话说明**：查看 research run 或 code-task run 的紧凑状态。

**语法用法**：

```bash
uv run simple-ar status runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | research run 或 code-task run 目录。 |

**生成产物**：

- 无文件写入；只打印状态

**注意**：

对 code-task run，会显示环境、计划、补丁、验证、benchmark、指标对比和 repair 状态。

## Tool 与 MCP

### `simple-ar tools schema`

**一句话说明**：导出真实已注册 tool 的 MCP 或 OpenAI function-tool schema。

**语法用法**：

```bash
uv run simple-ar tools schema --format mcp
uv run simple-ar tools schema --format openai --output tool_schema.json
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--format` | enum | `mcp` 或 `openai`，默认 `mcp`。 |
| `--output PATH` | path | 可选输出文件；省略时打印到 stdout。 |

### `simple-ar tools call`

**一句话说明**：调用一个 run-local 只读 tool，并写入紧凑 trace。

**语法用法**：

```bash
uv run simple-ar tools call runs/<run-id> list_experiment_artifacts
uv run simple-ar tools call runs/<run-id> search_generated_code --args-json '{"query":"run_experiment","max_matches":10}'
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | 已有 run 目录。 |
| `TOOL_NAME` | string | 已注册 tool 名称。 |
| `--args-json JSON` | object | JSON object 形式的 tool 参数。 |
| `--args-file PATH` | path | 从 JSON 文件读取 tool 参数。PowerShell 等 shell 中 inline JSON 不好转义时建议使用。 |
| `--debug-payloads` | flag | 保留更大的 trace payload；默认 trace 保持紧凑。 |

**生成产物**：

- stdout 上的 tool result JSON；
- `RUN_DIR/tools/tool_trace.jsonl`。

### `simple-ar tools serve-mcp`

**一句话说明**：通过 MCP stdio 暴露 run-local 只读 tools。

**语法用法**：

```bash
uv run simple-ar tools serve-mcp runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | tools 可检查的已有 run 目录。 |
| `--debug-payloads` | flag | 保留更大的 trace payload。 |

**注意**：

- 当前 server methods：`initialize`、`ping`、`tools/list`、`tools/call`；
- 默认只暴露真实注册的只读 experiment tools；
- 这个命令不会启用写文件、shell、network 或 dependency-install tool。

## Artifact Tools

### `simple-ar inspect`

**一句话说明**：索引并总结本地 run artifacts。

**语法用法**：

```bash
uv run simple-ar inspect runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | 要索引的 run 目录。 |

**生成产物**：

- `artifact_index.json`

**注意**：

只读取元数据和有界预览，不等于全文阅读；不再默认计算所有文件的哈希。
不追踪文件符号链接，也不打开命名管道等非普通文件。
`sha256` 默认为 null，Python 调用方可显式使用 `build_artifact_index(..., hash_files=True)`。
实验资产指纹与保护逻辑不变。

### `simple-ar search-artifacts`

**一句话说明**：使用 lexical retrieval 搜索已经索引的 run artifacts。

**语法用法**：

```bash
uv run simple-ar search-artifacts runs/<run-id> "accuracy" --top-k 5
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | run 目录。 |
| `QUERY` | string | 搜索 query。 |
| `--top-k N` | int | 返回结果数量，默认 `8`。 |
| `--include-operational` | flag | 同时搜索 manifest、runner metadata 等运行管理文件。 |

**生成产物**：

- 打印匹配片段和来源路径，并写入 `artifact_index.json`、`artifact_chunks.jsonl`、`artifact_search_results.json`。

**注意**：

搜索会自行建立当前索引，不必先运行 `inspect`。大于 8 MiB 的文件不参与全文分块，
在 `skipped_files` 和 CLI 中明确显示 `file_too_large`，不把文件前缀当完整来源。
二进制文件不分块。此限制只针对产物搜索，不改变表格分析或论文读取的限制；
需要搜索大日志时先缩小范围或导出所需日志。

### `simple-ar clean`

**一句话说明**：预览并清理某个 run 的可重建缓存，同时保留报告、manifest、paper metadata、read 阶段 Paper Brief、coverage report 和 `research_index/chunks.jsonl` 等审计产物。

**语法用法**：
```bash
uv run simple-ar clean runs/<run-id>
uv run simple-ar clean runs/<run-id> --yes
uv run simple-ar clean runs/<run-id> --all-caches
uv run simple-ar clean --shared-index
uv run simple-ar clean --shared-cache
```

**参数表**：
| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | 要清理的 run 目录。使用 `--shared-index` 或 `--shared-cache` 时可省略。 |
| `--yes` | flag | 跳过交互式 `yes` 确认，直接删除预览中列出的目标。 |
| `--all-caches` | flag | 在更强警告后，删除该 run 下所有已知可重建缓存、索引和 context artifacts。 |
| `--shared-index` | flag | 强清理：清空跨 run/test 共享的 research index store。 |
| `--shared-cache` | flag | 最强共享清理：同时清空共享 research index、literature provider cache 和外部 agent handoff archives。 |
| `--index-root PATH` | path | `--shared-index` / `--shared-cache` 使用的共享索引根目录；默认 `SIMPLE_AR_RESEARCH_INDEX_ROOT` 或 `.simple_ar_cache/research_index`。 |
| `--literature-cache-root PATH` | path | `--shared-cache` 使用的 literature cache 根目录；默认 `.simple_ar_cache/literature`。 |
| `--allow-external-index-root` | flag | 允许 shared cleanup 清理当前 workspace 外的路径。 |

**生成产物**：
- 先打印 Rich tree 预览：红色为将删除的缓存，绿色为会保留的审计产物。
- 当前会话按 `session_manifest.json`（schema `session_manifest.v2`）识别布局，清理 `cache/literature/` 中识别出的查询元数据 JSON 和 `artifact_search_results.json`；下载原文仍可能被保存的阅读直接引用，因此保留。
- 历史布局清理已知 `02-search/documents/fulltext_cache/`、`02-search/documents/extracted_text/` 等缓存。
- 如果 `index_meta.json` 指向当前 workspace 下的共享 SQLite research index，会删除该 run 对应的 SQLite rows。
- 使用 `--all-caches` 时，当前会话另清 artifact index/chunks；历史布局另清已知 research index、code-task repo map、locate outputs 和 context packs。

**注意**：
`clean` 不删除 run 目录本身。当前会话的 `attempts`、`outputs`、`documents`、下载原文及
未识别缓存继续保留，不追踪自定义外部缓存路径。
超过 1 MiB 检查边界的查询元数据会保留并列为未检查，不凭文件名删除。
历史布局保留报告、manifest、papers、解析审计、Paper Brief 和 synthesis brief；
其 `--all-caches` 会移除 portable 索引 chunks，
但仍保留最终报告、metadata、manifest 和 benchmark outputs。

`--shared-index` 比 `--all-caches` 更强：它会清空跨 run 共享的 SQLite/LanceDB 加速索引，后续运行需要重新构建索引状态。它不触碰 run 目录，因此 run-local 审计产物仍会保留。

`--shared-cache` 更强：它会同时清空共享 research index、`.simple_ar_cache/literature` 和 `.simple_ar_cache/agent_handoff_archives`。后续运行可能需要重新请求 literature provider、重新构建本地索引，并且不再保留旧的外部 agent handoff transcripts。

## Code Task Commands

Code-task 命令会把代码任务准备到 `runs/<run-id>/code_task/workspace`。已有项目默认 `auto`：优先创建 detached git worktree，Git 条件不满足时降级为受保护 copy；显式 `git_worktree` 失败时会给出可操作 checklist，而不是静默降级。greenfield 任务会从 empty workspace 开始生成项目。后续修改或生成只发生在隔离 workspace 的 project root 中，不会直接修改原始项目。

正常用户优先看“高级编排命令”。“底层原语命令”通常由 `execute` 自动调用，主要用于调试、学习或细粒度人工介入。

### 高级编排命令

已有项目的普通 `execute` 只使用一份 patch plan。通过 `--to-step work-plan` / `--to-step batch`
或已有 work plan 才进入分批路径，交互模式也相同；它们不是每次修改的必经阶段。

#### `simple-ar code-task init`

**一句话说明**：创建 code-task run，准备可编辑 workspace，并构建初始代码索引。

**语法用法**：

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task init --code-root path/to/project --task-file task.md --benchmark-command "python main.py"
uv run simple-ar code-task init --kind greenfield --task-file task.md --benchmark-command "python generated_project/main.py"
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--config PATH` | path | init 设置 TOML；CLI 参数覆盖配置值。 |
| `--kind MODE` | enum | `existing_project` 表示已有项目 patch；`greenfield` 表示从零生成项目。 |
| `--code-root DIR` | path | 源项目。`existing_project` 必填；`greenfield` 仅在需要 scaffold/source root 时填写。 |
| `--task-file PATH` | path | Markdown/text 任务描述。除非配置中已设置，否则必填。 |
| `--output-root DIR` | path | code-task run 创建位置。 |
| `--name TEXT` | string | run 名称后缀。 |
| `--benchmark-command TEXT` | string | 在 workspace 中 patch 前后运行的命令。 |
| `--primary-metric NAME` | string | before/after verdict 使用的主指标。 |
| `--metric-direction NAME=DIRECTION` | repeatable | 指标方向：`higher`、`lower`、`resource` 或 `ignore`。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | `--env-mode external` 的 Python。 |
| `--workspace-mode MODE` | enum | `auto`、`copy`、`git_worktree`、`sparse_copy` 或 `empty`。`greenfield` 默认 `empty`，已有项目默认 `auto`。 |
| `--workspace-include GLOB` | repeatable | `sparse_copy` include pattern。 |
| `--workspace-exclude GLOB` | repeatable | `sparse_copy` 额外 exclude pattern。 |
| `--workspace-reuse-source-venv` | flag | 检测并复用 source `.venv` Python。 |
| `--max-file-bytes N` | int | copy/sparse 模式最大复制文件大小，`0` 表示禁用。 |

**生成产物**：

- `code_task/manifest.json`
- `code_task/task.md`
- `code_task/workspace/`
- `code_task/meta/codebase_index.json`
- `code_task/meta/repo_map.json`
- `code_task/meta/repo_map_summary.md`

**注意**：

可复用设置建议写入 TOML，见 [配置参考](CONFIG_REFERENCE_zh.md#独立-code-task-config)。

#### `simple-ar code-task execute`

**一句话说明**：根据当前 run 产物推进到下一个安全停止点。

**语法用法**：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task execute runs/<run-id> --to-step propose-edits
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--config PATH` | path | 可选 TOML，用于模型路由、预算和运行设置。 |
| `--to-step STEP` | enum | 最多运行到 `probe`、`baseline`、`work-plan`、`batch`、`plan`、`propose-edits`、`apply-edits`、`review`、`validate`、`run`、`analyze-failure` 或 `repair`。 |
| `--dry-run` | flag | 只打印下一步动作，不写产物。 |
| `--model NAME` | string | LLM 步骤模型覆盖。 |
| `--no-llm` | flag | 尽可能使用 deterministic fallback。 |
| `--timeout N` | int | benchmark timeout。 |
| `--baseline-policy MODE` | enum | 已有项目 baseline 策略：`auto`、`run`、`skip`、`provided` 或 `none`。昂贵 baseline 可用 `skip`/`none` 跳过，或用 `provided` 记录已有指标。 |
| `--baseline-metrics-file PATH` | path | `--baseline-policy provided` 时读取的 JSON 或 `metric=0.82` 文本指标文件。 |
| `--planning-mode MODE` | enum | Greenfield 规划：`tool_agent` 分步规划并做有限审阅；`compact` 在重试前只调用一次架构规划。 |
| `--yes` | flag | 普通 execute 模式下自动批准 inline 审核门；与 `--interactive` 一起使用时，自动继续 primitive prompts。只有明确接受审核风险、想自动化跑通时才使用。 |
| `--interactive` | flag | 调试模式：逐个 primitive step 确认，而不是连续运行到下一个审核门。 |
| `--no-review-inline` | flag | 禁用 inline 审核提示，在审核门直接停止。 |
| `--skip-validation` | flag | 静态验证未通过时仍运行 benchmark。 |
| `--strict-validation` | flag | 将较高风险 validation warning 视为 error。 |
| `--validation-max-file-bytes N` | int | 静态验证扫描文件大小上限。 |
| `--apply-proposed-edits` | flag | plan 批准后应用已审核的 `proposed_edits.json`。 |
| `--allow-large-edits` | flag | 允许已审核、超过 normal 预算的较大 proposal。 |
| `--allow-planning-fallback` | flag | LLM 规划重试失败后，允许写入 deterministic fallback work/patch plan。 |
| `--llm-retry-attempts N` | int | work-plan、patch-plan、greenfield 架构/文件生成和 repair 的阶段级 LLM 尝试次数。 |
| `--repair-rounds N` | int | 失败后的 bounded repair proposal 轮数。 |
| `--max-files N` | int | LLM 步骤上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/work_plan.md` 和 `work_plan.json`
- `code_task/attempts/attempt-*/batches/batch-*/batch_state.json`
- `code_task/patch_plan.md`
- `code_task/meta/proposed_edits.json`
- `code_task/meta/applied_edits.json`
- `code_task/meta/review_report.json` 和 `review_report_post_run.json`
- `code_task/meta/validation_report.json`
- `probe` 后的 `code_task/meta/resource_probe.json` 和 `resource_decision.json`
- `code_task/memory/task_memory.md`、`compressed_memory.md` 和 `review_findings.jsonl`
- `code_task/run/baseline/`、`code_task/run/patched/`、`code_task/run/comparison.json`
- `code_task/summary.md`

**注意**：

`execute` 会保留审核点，但不强制每个审核门都另开一条命令。真实终端里，它会对
`patch_plan.md`、`proposed_edits.json` 或 large-edit approval 打印黄色 Rich 审核面板，
并询问是否继续；非交互 shell 中会干净停在审核门，除非显式传入 `--yes`。中断后重跑时，
已完成步骤会显示为 skipped。只有调试 primitive step 时才建议使用 `--interactive`，
并可搭配 `--yes` 自动继续这些 primitive prompts。普通 execute 模式下的 `--yes`
会自动批准审核门，只应在你明确想自动审批 plan/proposal 时使用。使用
`--no-review-inline` 可恢复“停住、下次再跑”的旧行为。完整运行流程见
[使用与配置](USAGE_zh.md#推荐路径toml--execute)。

如果 LLM work-plan 或 patch-plan 返回了无法解析的 JSON，`execute` 会停在
`llm_planning_failed`，并且不会写入 offline fallback plan。此时直接重跑同一条
`execute` 命令即可重新尝试模型调用；如果你明确接受 deterministic plan，再使用
`--no-llm` 或 `--allow-planning-fallback`。

补丁应用后，`execute` 会先运行结构化 reviewer，再进入静态验证；patched
benchmark 完成后还会再运行一次 post-run reviewer。阻塞性发现会写入
`code_task/memory/`，后续 repair 可以直接利用这些失败证据。

传入 `--config PATH` 时，`execute` 也会读取 standalone code-task 的
`[implementation]` 和 `[resource]`。Greenfield 任务正是通过这里选择本地
backend 或显式的 Codex / Claude Code / OpenCode handoff，而不是新增一套
provider-specific CLI 参数。

对于 greenfield 任务，`execute` 还会在 implementation planning 前写出
`code_task/meta/dependency_advice.json` 和 `.md`。JSON 会记录当前 Python 环境的
完整 installed distributions snapshot；终端输出和模型上下文只使用紧凑的任务相关子集。
这只是建议，不会自动安装依赖。

Greenfield 默认使用 `tool_agent` planning，会把 requirements、architecture、
interfaces、file plan 和 planning review 的中间产物写到
`code_task/meta/planning/`。需要减少规划调用时可使用
`--planning-mode compact`；后续文件生成、审阅与执行共用同一实现。

如果 greenfield review 发现通用可修复的 blocking finding，有限 repair 轮次会优先
生成结构化局部 action，例如唯一 old/new 替换或函数级替换；只有文件级结构错误时才
回退到整文件替换。修复会在继续 review/validation 前同步
`code_task/meta/code_artifacts.json`。如果 finding 仍然阻塞，执行会停住并保留生成文件和
review 报告。

#### `simple-ar code-task decide-plan`

**一句话说明**：记录当前 patch plan 的人工审核决定。

**语法用法**：

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--decision VALUE` | enum | `approve`、`reject` 或 `revise`，必填。 |
| `--note TEXT` | string | 可选审核备注。 |
| `--reviewer TEXT` | string | 审核人标签，默认 `user`。 |

**生成产物**：

- 更新 `manifest.json` 中的 plan decision 状态

**注意**：

当 patch plan 不应继续生成 proposal 时，使用 `reject` 或 `revise`。

### 底层原语命令

以下命令通常由 `execute` 自动调用。需要手动控制或排查某一步时再直接运行。

#### `simple-ar code-task map`

**一句话说明**：从 editable workspace 重建 repo-map 产物。

**语法用法**：

```bash
uv run simple-ar code-task map runs/<run-id> --show-summary
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--no-refresh-index` | flag | 复用已有 `codebase_index.json`。 |
| `--show-summary` | flag | 打印 `repo_map_summary.md`。 |

**生成产物**：

- `code_task/meta/codebase_index.json`
- `code_task/meta/repo_map.json`
- `code_task/meta/repo_map_summary.md`

**注意**：

确定性命令，不调用 LLM。

#### `simple-ar code-task locate`

**一句话说明**：从 repo map 中排序可能相关的可编辑文件和只读证据文件。

**语法用法**：

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve classifier"
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--query TEXT` | string | locate query，默认使用 `code_task/task.md`。 |
| `--top-k N` | int | 每组候选数量，默认 `8`。 |
| `--refresh-map` | flag | 排序前重建 index 和 repo map。 |
| `--no-read-only` | flag | 省略受保护的只读证据文件。 |
| `--show-summary` | flag | 打印 `locate_results.md`。 |

**生成产物**：

- `code_task/meta/locate_results.json`
- `code_task/meta/locate_results.md`

**注意**：

tests 和 benchmark 可作为计划证据，但默认不可编辑。

#### `simple-ar code-task context`

**一句话说明**：构建受限的 prompt-ready context pack。

**语法用法**：

```bash
uv run simple-ar code-task context runs/<run-id> --max-files 8 --max-total-chars 20000
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--query TEXT` | string | locate query，默认使用任务文件。 |
| `--top-k N` | int | locate 候选预算。 |
| `--max-files N` | int | 纳入 snippet 的文件上限。 |
| `--max-source-chars-per-file N` | int | 单文件 snippet 字符预算。 |
| `--max-total-chars N` | int | 总 snippet 字符预算。 |
| `--refresh-map` | flag | 打包前重建 repo map。 |
| `--show-prompt` | flag | 打印 `prompt_context.md`。 |

**生成产物**：

- `code_task/context_packs/context-NNN/context_pack.json`
- `code_task/context_packs/context-NNN/prompt_context.md`
- `code_task/context_packs/context-NNN/selected_snippets.jsonl`

**注意**：

不调用 LLM，也不修改文件。

#### `simple-ar code-task probe`

**一句话说明**：检查 workspace runtime 和项目环境信号。

**语法用法**：

```bash
uv run simple-ar code-task probe runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/meta/environment_report.json`

**注意**：

`probe` 不安装依赖，也不运行项目 benchmark。

#### `simple-ar code-task baseline`

**一句话说明**：patch 前运行记录的 benchmark。

**语法用法**：

```bash
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--command TEXT` | string | 覆盖本次 benchmark command。 |
| `--timeout N` | int | benchmark timeout。 |
| `--skip-validation` | flag | 静态验证未通过时仍运行。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/run/baseline/execution_report.json`
- `code_task/run/baseline/stdout.txt`
- `code_task/run/baseline/stderr.txt`
- `code_task/run/baseline/metrics.json`

**注意**：

benchmark command 在 `code_task/workspace` 中运行。

#### `simple-ar code-task work-plan`

**一句话说明**：生成面向批次执行的 implementation work plan。

**语法用法**：

```bash
uv run simple-ar code-task work-plan runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 使用 fallback planning。 |
| `--force` | flag | 重新生成已有 work plan。 |
| `--allow-planning-fallback` | flag | LLM work planning 失败时允许 deterministic fallback。 |
| `--llm-retry-attempts N` | int | work planning 的 LLM 尝试次数。 |
| `--max-files N` | int | planning 上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |

**生成产物**：

- `code_task/work_plan.json`
- `code_task/work_plan.md`

**注意**：

work-plan 中的 target files 会进入后续 edit-scope 检查。

#### `simple-ar code-task batch`

**一句话说明**：为某个 work-plan item 创建 attempt/batch 状态目录。

**语法用法**：

```bash
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--work-item ID` | string | work-plan item id，例如 `W1`，必填。 |
| `--attempt-id ID` | string | 可选 attempt id，例如 `attempt-001`。 |
| `--force` | flag | 即使已有 batch，也创建新 batch。 |

**生成产物**：

- `code_task/attempts/attempt-NNN/attempt_state.json`
- `code_task/attempts/attempt-NNN/batches/batch-NNN/batch_state.json`

**注意**：

`batch` 不调用 LLM，也不修改文件。

#### `simple-ar code-task plan`

**一句话说明**：为 active batch 生成可人工审核的 patch plan。

**语法用法**：

```bash
uv run simple-ar code-task plan runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 使用 fallback plan。 |
| `--force` | flag | 重新生成已有 plan。 |
| `--allow-planning-fallback` | flag | LLM patch planning 失败时允许 deterministic fallback。 |
| `--llm-retry-attempts N` | int | patch planning 的 LLM 尝试次数。 |
| `--max-files N` | int | 上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |

**生成产物**：

- `code_task/patch_plan.md`

**注意**：

生成 edit proposal 前应先运行 `decide-plan`。

#### `simple-ar code-task propose-edits`

**一句话说明**：请求模型生成受控 old/new text edits。

**语法用法**：

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 写入 deterministic empty proposal。 |
| `--force` | flag | 重新生成已有 proposal。 |
| `--max-files N` | int | 可编辑上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |
| `--allow-large-edits` | flag | 人工审核后接受较大但仍受限的 proposal。 |

**生成产物**：

- `code_task/meta/proposed_edits.json`
- `code_task/meta/proposal_warnings.json`
- 如果存在 active batch，也会写入 batch 级 proposal 产物

**注意**：

proposal 是结构化 JSON，不是 unified diff。

#### `simple-ar code-task apply-edits`

**一句话说明**：在 workspace 中安全应用受控 old/new edits。

**语法用法**：

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--edits-file PATH` | path | 指定要应用的 proposal 文件。 |
| `--allow-unapproved-plan` | flag | 本地测试/demo 时绕过 plan approval。 |
| `--allow-large-edits` | flag | 应用已审核、需要 large-edit approval 的 proposal。 |

**生成产物**：

- 修改 `code_task/workspace` 下的文件
- `code_task/meta/applied_edits.json`
- `code_task/patch.diff`

**注意**：

写文件前会检查路径、edit scope、old text 匹配和 large-edit 限制。

#### `simple-ar code-task validate`

**一句话说明**：对 workspace 运行轻量静态验证。

**语法用法**：

```bash
uv run simple-ar code-task validate runs/<run-id> --strict
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--strict` | flag | 将较高风险 warning 视为 error。 |
| `--max-file-bytes N` | int | 扫描文件大小上限。 |

**生成产物**：

- `code_task/meta/validation_report.json`

**注意**：

静态验证是保守检查，不替代 benchmark。

#### `simple-ar code-task run`

**一句话说明**：patch 后运行 benchmark。

**语法用法**：

```bash
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--command TEXT` | string | 覆盖记录的 benchmark command。 |
| `--timeout N` | int | benchmark timeout。 |
| `--skip-validation` | flag | 静态验证未通过时仍运行。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/run/patched/execution_report.json`
- `code_task/run/patched/stdout.txt`
- `code_task/run/patched/stderr.txt`
- `code_task/run/patched/metrics.json`
- baseline metrics 存在时写入 `code_task/run/comparison.json`

**注意**：

指标比较会区分“benchmark 跑通”和“目标确实提升”。

#### `simple-ar code-task analyze-failure`

**一句话说明**：总结最近失败的 validation 或 benchmark。

**语法用法**：

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |

**生成产物**：

- `code_task/run/patched/failure_analysis.md` 或 `code_task/meta/failure_analysis.md`

**注意**：

确定性命令，不修改源文件。

#### `simple-ar code-task repair`

**一句话说明**：根据最近失败上下文提出受限 repair edits。

**语法用法**：

```bash
uv run simple-ar code-task repair runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 写入 deterministic empty repair proposal。 |
| `--max-files N` | int | repair 上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |

**生成产物**：

- `code_task/repairs/repair-NNN/proposed_edits.json`
- 更新 `code_task/summary.md`

**注意**：

repair proposal 不会自动应用。审核后使用 `apply-edits --edits-file ...`。
