# 配置参考

[English version](CONFIG_REFERENCE.md)

首次使用从[引导设置](#引导设置)开始；完整字段表是可选专家参考。`.env` 保存全局连接，
任务 TOML 保存案例输入和执行限制。阅读/修订范围及证据限制见[工作流](WORKFLOWS_zh.md)，
不另增配置层；启用审阅不代表科学结论已核实。

## 已有材料写作

`start --kind writing --goal "说明已有结果和局限" --material notes.md --prepare-only`
自动保存普通配置，不必先写 TOML。高级配置使用 `task.kind = "writing"`、
`task.outputs = ["report"]`、`assets.materials = ["notes.md"]`、`model.name = "env"`。
`assets.papers` 可另提供论文；不要把同一文件重复标成论文和笔记。
材料路径相对配置文件解析，支持 Markdown、文本和 PDF。
`assets.materials` 还可接受已完成的 `table_analysis.v1` `analysis.json`，同目录保留数据副本。
沿用同一配置字段，不新增运行时：重新计算核对数值、生成原生 SVG，报告附可搬迁数据表与分析包。
写作导入的 JSON/数据文件各限 20 MiB；缺项或数值不一致会在写作前失败。
复算不证明采集或科学主张；原始表格应先使用 `data_analysis`，不能直接冒充分析包。

默认 `report.template = "material_report"`，整理已有材料，不要求不存在的实验或失败目标；
`"analysis_report"` 保留为目标未达成/不确定的实验分析，`"experiment"` 请求论文体草稿，而不授权实验。
已保存的显式模板继续有效；已经开始写作的自动模板沿用原输入快照的解析结果，不因升级重选。
引导写作默认开启 `report.document_review = true`。执行配置、在线搜索及研究创新不属于此任务。
提取后的文本随会话保存，恢复不重新读取修改过的原文件；更换材料应明确修订或新建任务。
提供的结果仍是外部陈述，不能称为本次独立测量；缺证据和缺书目信息必须保留，审阅通过不保证论文正确。

引导入口在线调研默认读取摘要和已提供的本地材料。加入 `--fulltext --sources search`
才允许远程全文抓取/PDF 下载；仍沿用现有材料流水线，获取失败会记录，不能当成读过全文。
本地材料模式不通过此选项开启网络。

## 引导设置

`simple-ar start` 支持 `survey`、`bug_fix`、准备好的 `reproduction` 和已有材料 `writing`，生成普通研究 TOML；代码任务额外生成
CodeTask TOML 和任务说明。沿用默认值、TOML、显式 CLI 的覆盖关系，不新增配置体系。

```bash
simple-ar start --kind survey --goal "比较不确定性估计方法" --sources search --prepare-only
```

`--sources materials` 需要可重复的 `--document PATH`，禁止在线搜索。
`bug_fix` 需要 `--project`、`--validate` 和可重复的 `--allow` 编辑范围；默认复制工作区、
当前 Python、300 秒验证超时、一次修复，保护测试和 `.env`。复制不是 OS 沙箱，执行
验证命令仍需授权；自定义环境、范围和时限可修改生成的 CodeTask TOML。

固定协议复现需提供本地论文、要检查的结论、数据/条件偏离、判断标准及指标。
环境、数据和命令必须已准备好；引导不会自动安装、发明方法或训练 baseline。例如：

```bash
simple-ar start --kind reproduction --goal "检查论文的覆盖率结论" \
  --document paper.pdf --hypothesis "已知权重在协变量偏移下保持覆盖率" \
  --dataset "已准备的合成适配数据" --expected-outcome "比较覆盖率与标称 0.9" \
  --metric coverage --cwd ./prepared-project --timeout-sec 300 --prepare-only \
  --command python run.py --seed 7
```

`--command` 必须放最后，其后全是进程参数，不是引导选项，也不经过 shell 解释。
交互时用 JSON 参数列表输入命令。默认当前目录/环境、一次执行、300 秒时限，生成复现报告并
进行全文审阅；API 累计额度仍不设限。`--metric` 可重复，第一项为主指标；命令必须按现有执行器
支持的方式输出指标（例如 `coverage: 0.91`）。更复杂的结果格式、协议条件或明确增加资源额度，
可在执行前编辑生成的 TOML。这是论文结论检查，不是自动准备环境或完整论文复现。

输入保存到 `--output-root`（默认 `runs/assistant`）；资产路径为绝对路径，产物目录
`sessions` 相对生成的研究 TOML 解析。设置过程不改原项目，不把凭据写进配置。
API 总额仍默认无限制。

`--prepare-only` 不调用模型/进程。`--yes` 只确认启动，执行期仍遵循 `--interaction`
（默认 `checkpoints`）。非交互缺输入就报错，不挂住等待；最后拒绝执行会保留配置。
开始执行后按打印的路径使用 `research-session --session-root PATH --model MODEL` 续跑，
保留设置时选定的模型（默认 `env`），不要再次 `start` 创建新任务。
数据分析引导保存 `model.name = ""`，其续跑命令省略 `--model`。

研究任务使用 `simple-ar research-session --config PATH`。可直接从
[综述案例](../examples/survey/README.md)开始；更多配置项在下文说明，不再作为额外案例混放。
CodeTask 专用选项仍通过下方 CodeTask TOML 复用。
旧八阶段外层配置解析器及别名转换已经退出，历史快照仍可读取，但不是可执行工作流。

## 已有数据的描述分析

```toml
[task]
goal = "描述已有测量"
kind = "data_analysis"
outputs = ["data_analysis"]

[analysis]
file = "observations.csv"
value_columns = ["score"]
group_column = "method"
observation_unit = "一次运行"
value_unit = "秒"
```

`file` 相对 TOML 解析；`value_columns` 与非空 `observation_unit` 必填。
`group_column`、`value_unit` 默认空（不分组、单位未知）。可选：`mode = "observations"`
计算 count/mean/sample std，或 `"values"` 保留汇总值并要求唯一标签；`missing = "reject"`
或明确 `"omit"`；`width = "wide"` 或 `"column"`；物理限制 `max_mb = 20`、`max_figures = 100`
为可调整正整数。缺失不填零，非有限/非数值报错；不自动造误差条，不作显著性/因果结论。
任务只接受 `data_analysis` 输出，不接受执行或文献选项，不需要或使用模型配置。
`plot` 默认 `"bar"`；`"line"`/`"scatter"` 要求 `mode = "values"` 和数值 `x_column`，
不设置 `group_column`，`x_unit` 默认空（单位未知）；多个数值列分别绘图。
折线要求 x 唯一并按 x 排序，缺失 y 断线；散点保留重复 x，不聚合、不拟合。
即便 `missing = "omit"`，x 也必须完整。坐标图物理上限 `max_points = 10000` 为可调整正整数，
超出报错而非抽样。见[完整案例](../examples/data-curves/README.md)。
新任务保存前按 `max_mb` 预检表格结构与所选列；正式摄入仍校验数值，续跑不重读原文件。
摄入时固化原始字节与设置，续跑复用；更改列/聚合设置需新任务。产物与重建见[工作流](WORKFLOWS_zh.md)。

## 全局 `.env`

`.env` 由 LLM 集成读取，用于凭据、endpoint、模型和传输设置；它被 Git
忽略，不应放任务资源、源码根目录、数据集或解释器路径。任务专属路径和执行条件
必须写在研究 TOML 或其引用的 CodeTask TOML 中。

## 研究 TOML：分区与默认值

研究入口的 `report.template` 默认 `auto`：仅调研且最多只有一份可引用来源时，使用简短的单来源证据审阅；多来源才使用综述结构，不凭单篇材料虚构方法谱系或跨论文结论。独立供材写作自动选 `material_report`，具体恢复语义见上文。实验任务依据分析中的目标判断选择实验论文、复现报告或简短实验分析报告。执行 `passed` 不等于科研目标达成；目标不明、未达成或因轮次限制停止时，默认不写成成功论文。显式 `source_review`、`survey`、`material_report`、`experiment`、`reproduction`、`analysis_report` 或自定义模板优先，但不能覆盖测量事实。旧会话保存的显式模板不会被新默认值更换；需要时通过现有报告配置续接改为 `auto`。结构自动选择不代表论文语义质量已验收。

新研究会话不设置 `budget.total_tokens`、`budget.llm_requests` 时，框架不限制 API 总 token 和请求次数；
需要限制时设置正整数。无限额仍记录用量，不等于免费调用或无限重试。
进程资源、科研轮次、attempt/no-progress 和供应商单次输出限制独立保留。
恢复旧会话仍沿用其持久化额度；省略配置不会清除旧限额或清零用量。服务商报错时保留产物并暂停受影响工作，以便恢复。

- 优先级：内置默认 → 研究 TOML → 显式 CLI 覆盖。CLI 列表覆盖整份文件列表。
- 研究文件中的相对路径以 TOML 所在目录为基准；命令 argv 原样传给实验进程。

### 任务、模型与预算

| 分区 | 字段 | 默认值 / 必填与条件约束 |
| --- | --- | --- |
| `[task]` | `goal`、`kind`、`outputs`、`output_root`、`selected_idea_id` | 新 session 必须有 `goal`。kind：默认 `auto`，或 `survey`、`bug_fix`、`measurement`、`reproduction`、`writing`、`data_analysis`。`measurement` 只输出 experiments；`reproduction` 含 experiments，可加 report；`writing` 只接受 report；`data_analysis` 只接受同名输出。其他输出：summary/report/experiments/bug_fix。`output_root` 默认 runs/research-session；可选 `selected_idea_id` 必须选已有、具有依据的候选。 |
| `[model]` | `name`、`feasibility_review_model`、`max_output_tokens` | 文件配置默认 `name = "env"`，读取 `.env` 的 `SIMPLE_AR_MODEL`；`name = ""` 选择不调用 LLM 的确定性处理。可选 `feasibility_review_model` 仅让另一模型审核源码支持的实现可行性，仍使用同一服务商与会话预算；选择会随会话保存，恢复时不可更改。`max_output_tokens` 可省略；凭据始终留在环境中。 |

独立可行性审查可在 CodeTask 或训练前质疑方案机制；它仍是模型判断，不能替代可执行的机制验证或改进证据。省略时由主模型审查；续接时可省略该字段以沿用存档选择。
| `[budget]` | `total_tokens`、`llm_requests`、`process_invocations`、`process_wall_seconds` | 新 session 的 token/request 上限可省略（该维度不设框架上限）；进程值按任务形态在入口推导，要求执行时应显式设置。恢复沿用已存账本，不清零用量。 |

### 研究输入与行为

| 分区 | 字段 | 默认值 / 必填与条件约束 |
| --- | --- | --- |
| `[research]` | `providers`、`queries`、`max_results`、`max_chunks`、`max_pdf_pages`、`read_max_shortlist`、`idea_limit`、`cache_dir` | 列表可省略；CLI 默认 `max_results = 10`、`max_chunks = 300`、`idea_limit = 3`。`max_pdf_pages` 是正整数，限制本地 PDF 最多提取页数（默认 `20`）；更改后应创建新会话，不能把已冻结的阅读证据当成新版本。`read_max_shortlist` 可选，显式提供的论文优先保留；若数量超过上限则显式报错。`cache_dir` 可选，未持久化，不能作为安全的恢复变更。 |
| `[research]` | `use_fulltext`、`allow_pdf_download`、`keep_raw_pdf`、`max_fulltext_documents`、`max_pdf_mb`、`materials_only` | 开关默认 false，可选上限默认不设。`materials_only = true` 使用本地输入（`assets.papers`，或写作的 `assets.materials`）并禁用 search，仍允许模型阅读；writing 始终仅用本地输入。引导入口的 `--fulltext --sources search` 会允许缓存 PDF，默认最多 4 份、每份 20 MiB；专家可在 TOML 中调整正整数上限。远程 PDF 需要下载许可与缓存许可；获取失败仍明确标注只读摘要或不可用。 |
| `[research]` | `max_iterations`、`interaction` | `max_iterations` 默认 `1`，`0` 表示首轮分析后停止。`interaction` 新 CLI 默认 `checkpoints`，可选 `assisted`、`checkpoints`、`autonomous`；硬事实和权限缺口在任何模式下都是阻塞。 |
| `[assets]` | `papers`、`materials` | 只读本地 Markdown/text/PDF 路径，相对 TOML 所在目录解析。`papers` 表示书目来源；`materials` 用于 `writing` 的草稿、笔记、外部结果说明或附有数据副本的完整 `table_analysis.v1` 分析包，不当成本次实测实验指标。写作至少需要一份输入，不接受重复文件或同一文件兼任两种角色。 |

未知分区/字段和类型错误会显式拒绝。accepted plan 是短顺序计划，动作唯一且输入由
能力适配函数绑定，不是任意模型调度器。只调研不会创建实验进程；`bug_fix` 必须提供
`execution.code_task_config`，产出 `bug_fix` 且不运行 baseline；`auto` 不意味着自动发现
仓库或安装依赖。

### 执行与报告

| 分区 | 字段 | 默认值 / 必填与条件约束 |
| --- | --- | --- |
| `[execution]` | `command`、`cwd`、`timeout_sec`、`code_task_config` | 选择一个执行边界：literal argv `command` 加已存在的绝对 `cwd`，或 CodeTask TOML 引用。只调研时两者都省略；`timeout_sec` 在 CLI/应用边界提供默认值。 |
| `[execution]` | `primary_metric`、`metrics`、`metric_directions` | 可选测量 schema；方向为 `higher`、`lower`、`resource` 或 `ignore`。 |
| `[execution]` | `pairs`、`seeds`、`seed_flag`、`seed_count` | 可选的显式比较输入。`pairs` 每行包含唯一整数 `seed` 与 literal `baseline_command`/`candidate_command`；compact seed 必须有 literal command 和显式 seed flag/count，不解析自然语言 seed。 |
| `[execution]` | `baseline_policy`、`baseline_ref`、`protocol` | policy 为 `run`、`skip` 或 `reuse`；`reuse` 要求当前 session 中通过且命令、schema、协议条件、保护资产和准备 lineage 都匹配的产物。`protocol` 复用已有实验合同，但不证明数据内容。 |
| `[report]` | `template`、`reviewer`、`max_review_iterations`、`document_review`、`max_section_tokens`、`max_cited_sources`、`figures` | `template` 默认 `auto`，`reviewer` 默认 `llm`，CLI 修订次数默认 `1`。可选 `document_review = true` 增加有界整稿审查，最多选择两处，每处最多修订 `max_review_iterations` 次；被拒候选也计入额度，恢复不重置。整稿审查默认关闭。`max_section_tokens = 0` 取消单次输出上限；可选正整数 `max_cited_sources` 限制最终报告的不同引用数，不提前截断检索/阅读候选，超出则终审失败；省略即不设此上限。图表默认使用确定性图表，可设 `[report.figures].enabled = false` 或 `mode = "off"`。 |

单条固定命令只写 `seed_flag` 会记录当前 seed，但不授权增加新 seed。若希望先只运行 seed 0、以后允许按证据决定是否补测，可同时写 `seeds = [0]` 和 `seed_flag = "--seed"`；这不会默认多跑种子。补测仍须分析提出理由、运行同种子的 baseline/candidate 配对、通过剩余进程预算检查并由既定交互模式接受。

`[[execution.protocol.protected_assets]]` 中每个文件须有唯一 `asset_id` 和 `path`。运行前后会检查这些文件是否发生改动。相对路径按实验 `cwd` 解析；只核对显式列出的文件，不递归校验整个数据集，也不因此证明科研结论正确。

显式 `outputs` 不能与 `--with-report`/`--no-report` 同时使用。报告结构选择不能覆盖
测量事实或证明科研成功；恢复时变更搜索/摄取设置若与存档不符会被拒绝，显式报告变更只
失效 writer/report/audit 产物，不重跑研究或测量。已有前缀可用 `research-report` 补齐报告。

`task.kind = "reproduction"` 是**已准备好环境与命令的固定协议复现**：读取本地论文、
综合来源证据、执行声明的命令、分析实测值，可选复现报告。要求 `research.materials_only = true`、
`assets.papers`、`execution.command`，以及至少包含 `hypothesis`、`dataset`、`expected_outcome`
的 `execution.protocol`；使用 `baseline_policy = "skip"` 和有限进程超时。
它不提出创新、不改代码、不扩种子，也不自动安装或寻找缺失环境。配对对照和 CodeTask
仍使用普通研究路径。报告使用 `template = "reproduction"`，明确区分原论文结果、改编检查和本地实测。
完整低开销案例见 [conformal_reproduction](../examples/conformal_reproduction/README.md)。

`task.kind = "measurement"` 仅用于原样运行并分析一条已提供的命令，不做文献检索、
候选设计、CodeTask 改码或 baseline 对照。它要求 `outputs = ["experiments"]`、
显式 `execution.command` 和有限进程额度。需要依据证据提出新候选或做对比实验时，
使用 `auto`/科研路径；单次测量不证明科研改进。

- code-task init --config PATH 读取初始化配置。
- code-task execute --config PATH 读取执行、模型与预算配置。
- research-session --code-task-config PATH 使用同一个 CodeTask 解析器。
- 显式 CLI 参数覆盖对应 TOML 选项。
- `[continuation]` 除额度授权外，还接受 `decision_id`、`decision_response` 和 `decision_guidance`，
  用于通过正式续接入口答复 Rich 显示的待处理决定。
- 安装与命令说明：[使用手册](USAGE_zh.md)、[CLI参考](CLI_REFERENCE_zh.md)。

### 研究会话补齐条件与续接

```bash
simple-ar research-session --config research.toml --session-root runs/research-session/<session>
```

省略的目标和 outputs 沿用原值；显式提供的新目标、outputs、本地文献或执行配置会修订现有
session。accepted plan 重新生成，attempt 历史保留；只有命令、结果 schema、协议、准备 lineage
和保护资产匹配的测量才复用。仅增加文献不会重跑有效实验；改变 task kind 仍需新建 session。

待处理的科研决定通过同一 CLI 入口答复；无需交互式 stdin：

```bash
simple-ar research-session --session-root runs/research-session/<session> --topic "原研究目标" \
  --decision-id ID_FROM_RICH --decision-response accept

simple-ar research-session --session-root runs/research-session/<session> --topic "原研究目标" \
  --decision-id ID_FROM_RICH --decision-response revise \
  --decision-guidance "补充事实或修订后的方向"
```

`decision_response` 为 `accept`、`reject` 或 `revise`。修订自动交付选择时，还要显式提供报告设置，
例如 `--report-template analysis_report`。TOML 答复写法：

```toml
[continuation]
decision_id = "ID_FROM_RICH"
decision_response = "revise"
decision_guidance = "补充事实或修订后的方向"
```

精确重放同一答复不会重复已完成工作；提案和答复作为分开的决策产物保留。改变目标或协议后，
旧决定不会沿用。`assisted` 会询问科研后续选择，`checkpoints` 只暂停在首次协议、实质方向/交付节点，
`autonomous` 在有依据且仍有授权轮次时自动选择；它们都不能越过缺失事实或权限。

### 显式续接额度

在同一 `research-session --session-root` 命令中可用 TOML 授权：

```toml
[continuation]
authorization_id = "review-20260924-1"
reason = "审查后的有界续接"
additional_attempts = 2
additional_no_progress = 1
remaining = { total_tokens = 50000, llm_requests = 8 }
```

`remaining` 对原账本已有的资源维度开启一份后续调用的新剩余额度；它不会估算或消除未知历史用量，
旧 ledger entries 和 attempt 计数仍保留。`additional_attempts`、`additional_no_progress` 增加持久化
上限，不清零已用量。CLI 等价选项是 `--authorization-id`、`--authorization-reason`、可重复的
`--authorize-remaining DIMENSION=AMOUNT`、`--additional-attempts`、`--additional-no-progress`。
相同 ID 只可重放完全相同的条款；需要新授权时使用新 ID，不要手改 ledger/manifest。
已完成会话仅追加额度时保持完成状态，不新增 attempt 或修改产物；授权不等于执行。
可先补资源额度，再补 attempt 额度；执行仍检查所有实际需要的上限。
重放相同条款不会补回已消耗额度；若上次续接在账本写入中断，会幂等补完该账本项。
报告刷新先完成授权，再调用 `research-report --refresh`；纯报告无需扩大进程权限。

保存的研究/报告设置继续以 session runtime_config 为准，除非通过入口显式修订当前支持的输入。
首次启动时仍应声明允许的 `process_invocations`/`process_wall_seconds`；续接只接受此会话账本已配置的
资源维度。自动寻找、下载和配置仓库仍未实现。

研究准备复用 CodeTask 的 auto/copy/git_worktree 选项及自定义保护路径。
auto 对干净仓库使用 worktree；有未提交源码或无法创建 worktree 时使用 copy 并记录原因。
显式 git_worktree 使用已提交 HEAD，不包含未提交修改。准备产物记录 Git 版本和隔离位置；
这不是每轮候选自动提交 Git。sparse_copy/empty 仍限独立 CodeTask 使用。

## CodeTask TOML 字段参考

CodeTask TOML 旧有的相对路径仍以运行时 cwd 为基准，不会被静默改写。若要让案例随仓库
直接运行，可在路径字段和 `[benchmark].command` 中用 `{config_dir}` 引用该 TOML
所在目录；机器拥有的项目、数据集、划分或解释器则写显式绝对路径。命令参数中的路径
可能含空格时须加引号。可用 `[environment].required_paths` 在配置加载时检查额外
数据/划分文件是否存在；这不验证数据内容。完整用法见[持续学习案例](../examples/continual_learning/README.md)。

| 字段 | 含义 |
| --- | --- |
| `[code_task].kind` | `existing_project` 表示已有源码项目 patch；`greenfield` 从 empty workspace 开始，并在 `code_task/workspace/generated_project` 下生成项目。 |
| `[code_task].code_root` | `existing_project` 的源项目路径；`greenfield` 仅在需要 scaffold/source root 时填写。原始项目不会被直接修改。 |
| `[code_task].task_file` | 独立 CodeTask 的任务描述；research-session 通过正式应用准备 implementation handoff。 |
| `[benchmark].command` | 在 `code_task/workspace` 中 patch 前后运行的命令。建议输出 `accuracy: 0.82` 这类可解析指标。 |
| `[benchmark].primary_metric` | objective verdict 使用的主指标。未知指标仍会记录，但最好声明方向。 |
| `[benchmark.metric_directions]` | 指标方向表：`higher`、`lower`、`resource` 或 `ignore`。 |
| `[environment].mode` | `current` 使用当前 SimpleAutoResearch Python；`external` 使用 `[environment].python` 或 `[environment].python_executable`。不会自动安装依赖。 |
| `[environment].python_executable` | `mode = "external"` 时必填；会把 benchmark argv 开头的裸 `python`/`python3` 解析为该解释器。已经是绝对路径的命令和非 Python 命令保持不变。 |
| `[environment].required_paths` | 可选的文件/目录列表；加载 CodeTask 配置时检查是否存在，可用于数据和固定划分。存在性不等于数据内容已验证。 |
| `[workspace].mode` | workspace 策略：`auto`、`copy`、`git_worktree` 或 `sparse_copy`。已有项目默认 `auto`，会优先尝试 git worktree，失败时降级为 copy 并记录原因。 |
| `[workspace].reuse_source_venv` | 检测到 source `.venv` 或 `venv` 时，是否记录并使用其中 Python。 |
| `[implementation].provider` | code-task 实现 backend。`local` 使用 SimpleAutoResearch 进程内路径；`fake` 用于确定性测试；`local_llm` 使用当前 LLM；`codex`、`claude_code`、`opencode` 和 `external_cli` 会在显式启用时走外部 agent handoff 边界。 |
| `[implementation].agent_mode` | backend 实现模式：`model` 表示 SimpleAutoResearch 仍拥有 harness；`handoff` 会从外部 agent package ingest candidate files；`delegated_workspace` 目前只识别并显式失败，等 snapshot/diff/rollback 执行边界完成后再开放。 |
| `[implementation].allow_external_agent` | 外部 CLI backend 启动前必须显式设为 true。外部输出仍需通过 SimpleAutoResearch 的 review、validation、benchmark 或 result guard 才能被接受。 |
| `[implementation].agent_model` / `.agent_binary` / `.agent_args` / `.agent_timeout_sec` | 可选外部 backend 启动设置。除非确认外部 CLI/账号支持某个模型名，否则建议让 `agent_model` 留空。 |
| `[implementation].max_repair_attempts` | implementation 侧修复尝试上限，也覆盖 greenfield validation 前的 review repair。 |
| `[resource].max_runtime_sec` | 暴露给 greenfield planning 和 external-agent handoff 的运行预算。 |
| `[resource].max_files` / `.max_generated_lines` | code-task 生成预算。`code-task execute` 会优先使用 `[execute].max_files` 和 `[execute].max_generated_lines`，缺省时再读取这里。 |
| `[resource].max_memory_mb` / `.allow_gpu` | 暴露给规划阶段的内存/GPU 约束，只描述允许的资源 profile，不会自动安装依赖。 |
| `[edit_scope].allowed_patterns` | automated edits 可以修改的 workspace-relative glob allowlist。空列表表示所有 normalized、非 protected workspace 路径都可编辑。 |
| `[edit_scope].protected_patterns` | 额外只读路径。tests、benchmark、`.env`、secret/credential-like 路径等默认 protected patterns 始终保留。 |
| `[edit_scope].mode` | 可选标签，写入 `manifest.json` 供审计使用；它本身不改变行为。 |
| `[safety].max_file_bytes` | copy/sparse 模式最大复制文件大小，避免误复制大模型、数据或 checkpoint。 |
| `[safety].validation_max_file_bytes` | 静态 validation 扫描文件大小上限。 |

### Execute 与 Budget 字段

| 字段 | 含义 |
| --- | --- |
| `[execute].to_step` | 状态感知 executor 最多推进到哪一步。例如设为 `propose-edits` 可停在应用补丁之前，设为 `review` 可停在应用补丁后的结构化 review 之后。 |
| `[execute].use_llm` | 是否启用 LLM 支持的 work-plan、patch-plan、edit-proposal 和 repair 步骤。 |
| `[execute].timeout_sec` | executor 管理的 baseline 和 patched benchmark timeout。省略时会依次回退到 `[benchmark].timeout` 和 `[resource].max_runtime_sec`。 |
| `[execute].stream_benchmark_output` | 实时 benchmark log 模式：`off`、`line`、`auto` 或 `summary`。有 tqdm 这类进度条时建议 `auto`。 |
| `[execute].baseline_policy` | 已有项目 baseline 策略：`auto`/`run` 会运行未修改 benchmark；`skip`/`none` 不做 baseline comparison；`provided` 从 `[execute].baseline_metrics_file` 记录用户已有指标。 |
| `[execute].baseline_metrics_file` | `baseline_policy = "provided"` 时使用的 JSON 或 `metric=0.82` 文本指标文件。JSON 支持 `{"accuracy": 0.8}`、`{"metrics": {...}}` 和 `{"metric_values": {...}}`。 |
| `[execute].apply_proposed_edits` | 允许 execute 应用已经审核过的 proposal。review-first 流程建议保持 false。 |
| `[execute].allow_large_edits` | 允许应用超过 normal 预算但落在 large 预算内的已审核 proposal。 |
| `[execute].allow_planning_fallback` | LLM work-plan / patch-plan / greenfield 架构规划 / greenfield 文件生成重试后仍失败时，是否允许 deterministic fallback。真实 LLM run 建议保持 false，这样坏输出会安全停止并可重跑。 |
| `[execute].planning_mode` | Greenfield 规划：`tool_agent` 分需求、架构、接口、文件计划及有限审阅，正常至少五次调用；`compact` 在重试前仅一次架构调用。两者共用文件生成和执行，调用数不代表研究质量。 |
| `[execute].planning_review_rounds` | standalone code-task 中 greenfield planning reviewer 可触发的最大回修轮数。默认 `2`；轻量 smoke example 可设为 `1`，大型服务器任务可按需调大。 |
| `[execute].llm_retry_attempts` | work-plan、patch-plan、greenfield 架构规划和 greenfield 文件生成的 LLM 尝试次数；全部失败后才停止或显式 fallback。 |
| `[execute].repair_rounds` | 技术性验证/运行失败后的有限修复次数（非负整数，默认 `0`）。带 CodeTask 项目的 `research-session` 也用它限制单条件科研修订失败后的修复/复测；复测沿用既定协议与进程预算，不授权科研方法变更、增种子或配对修订的自动修复。修复仍需审核。 |
| `[execute].max_files` | plan/proposal/repair 步骤纳入 LLM 上下文的最大文件数。 |
| `[execute].max_source_chars_per_file` | LLM 上下文中单个文件的 source snippet 字符预算。 |
| `[execute].max_generated_lines` | greenfield 生成行数预算。省略时会回退到 `[resource].max_generated_lines`，再回退到保守默认值。 |
| `[models.code_task].planner` | work-plan 和 patch-plan 使用的模型。 |
| `[models.code_task].editor` | edit proposal 使用的模型。 |
| `[models.code_task].repair` | 失败后 repair proposal 使用的模型。 |
| `[budget].profile` | 当前 edit budget profile。`normal` 保守，`large` 用于已审核的多文件修改。 |
| `[budget].max_batches` | 一个 code task 中 executor 最多创建多少个实现批次。 |
| `[budget].cost_cap_usd` | provider usage 返回费用估计时可用的成本上限。 |
| `[budget.*].max_files` | 单个 edit proposal 最多修改多少个文件。 |
| `[budget.*].max_edits` | 单个 proposal 最多包含多少个 old/new replacement。 |
| `[budget.*].max_old_chars` / `[budget.*].max_new_chars` | 单个 edit 的 old/new 文本字符限制。 |
| `[budget.*].max_total_edit_chars` | 全部 edits 的总字符预算。 |
| `[budget.*].max_proposal_chars` | 序列化 proposal 的总字符预算。 |

## Workspace Mode Variants

### `auto`

`auto` 是已有项目推荐默认模式。它会在 `code_root` 位于本地 Git 仓库且仓库至少有一次 commit 时优先创建 detached `git_worktree`；如果 Git 条件不满足，则降级为受保护的 `copy`，并在 manifest 与终端输出中记录 `fallback_reason` 和 `user_next_steps`。

```toml
[workspace]
mode = "auto"
reuse_source_venv = false
```

如果 `code_root` 是较大仓库中的项目子目录，SimpleAutoResearch 会在仓库根创建 worktree，并把对应子目录作为后续索引、修改和运行的 project root。

### `copy`

`copy` 会在 `code_task/workspace` 下创建受保护的物理复制。它适合非 Git 项目、尚未提交但希望包含当前文件状态的实验，或者你明确不想使用 Git worktree 的场景。

```toml
[workspace]
mode = "copy"
reuse_source_venv = false

[safety]
max_file_bytes = 2000000
```

### `git_worktree`

`git_worktree` 会创建 detached worktree。显式选择该模式时，如果 Git 隔离不可用会直接失败并给出修复提示，而不会静默降级。`code_root` 可以是仓库根目录，也可以是仓库中的项目子目录；源项目必须位于本地 git 仓库中，并至少有一次 commit；不需要连接远程 GitHub 仓库。

```toml
[workspace]
mode = "git_worktree"
reuse_source_venv = true
```

### `sparse_copy`

`sparse_copy` 只复制 allowlist 路径，并始终应用内置排除规则：`.git`、虚拟环境、`runs`、cache/build 目录、data/model 目录、`.env` 和 secret-like 路径。

```toml
[workspace]
mode = "sparse_copy"
include = ["src/**", "tests/**", "configs/**", "main.py", "pyproject.toml"]
exclude = ["data/**", "models/**", "checkpoints/**"]
reuse_source_venv = false
```

只有在你理解项目依赖关系时才建议使用 `sparse_copy`；它可能漏掉运行时 import 需要的文件。

## 独立 Code-Task Config

用于 `simple-ar code-task init --config PATH`，后续也可传给
`simple-ar code-task execute RUN_DIR --config PATH`。

```toml
[code_task]
kind = "existing_project"     # existing_project | greenfield
code_root = "path/to/project"
task_file = "tasks/improve_model.md"
output_root = "runs"
name = "my-code-task"

[benchmark]
command = "python main.py --config configs/experiment.json"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
macro_f1 = "higher"
latency_ms = "resource"
loss = "lower"

[environment]
mode = "current"
# mode = "external"
# python = ".venv/Scripts/python.exe"

[workspace]
mode = "copy"
reuse_source_venv = false

[safety]
max_file_bytes = 2000000

[execute]
to_step = "run"
use_llm = true
timeout_sec = 120
repair_rounds = 1
stream_benchmark_output = "auto"
baseline_policy = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
planning_mode = "tool_agent"
planning_review_rounds = 2
llm_retry_attempts = 3
max_files = 8
max_source_chars_per_file = 4000
max_generated_lines = 1600

[implementation]
provider = "local"            # local | fake | local_llm | codex | claude_code | opencode | external_cli
agent_mode = "model"          # model | handoff | delegated_workspace
allow_external_agent = false
agent_model = ""              # 留空表示使用外部 CLI / 账号默认模型
agent_binary = ""
agent_args = []
agent_timeout_sec = 600

[resource]
max_runtime_sec = 120
max_files = 8
max_generated_lines = 1600
max_memory_mb = 4096
allow_gpu = false

[budget]
profile = "normal"
max_batches = 3

[budget.normal]
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000
```

独立 greenfield code-task 不需要填写 `code_root`，除非你有意从 scaffold/template
目录开始。workspace 默认使用 `empty`，`execute` 会把生成项目写到
`code_task/workspace/generated_project`：

```toml
[code_task]
kind = "greenfield"
task_file = "tasks/build_new_project.md"
name = "greenfield-project"

[benchmark]
command = "python generated_project/main.py"
primary_metric = "accuracy"

[workspace]
mode = "empty"

[execute]
to_step = "run"
max_files = 16
max_generated_lines = 4000
baseline_policy = "none"

[implementation]
provider = "local"
agent_mode = "model"
allow_external_agent = false

[resource]
max_runtime_sec = 600
max_files = 16
max_generated_lines = 4000
allow_gpu = false
```

如果要用同一个 greenfield 任务测试 Codex、Claude Code 或 OpenCode handoff，
保留 `[code_task]`、`[benchmark]` 和 `[resource]`，只切换实现 backend：

```toml
[implementation]
provider = "codex"
agent_mode = "handoff"
allow_external_agent = true
agent_model = ""          # 使用外部 CLI / 账号当前配置的模型
agent_timeout_sec = 1800
```

外部 agent 写出的仍然只是未信任候选文件。SimpleAutoResearch 会先 ingest，
再复制到 `code_task/workspace/generated_project`，之后继续走 review、validation、
benchmark、guard、memory 和 repair 路径。

## 在研究会话中使用 CodeTask

通过 research-session --code-task-config 提供现有项目 CodeTask TOML。
CodeTask 负责编辑与验证，应用负责实验测量和报告交付。

## Execute 与 Budget

`execute` 是状态感知调度器。下面配置控制它最多推进到哪一步、使用哪些模型、纳入多少上下文，以及允许多大的 edit proposal。

```toml
[execute]
to_step = "run"
use_llm = true
timeout_sec = 60
repair_rounds = 1
max_files = 8
max_source_chars_per_file = 4000
stream_benchmark_output = "auto"
baseline_policy = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
planning_review_rounds = 2
llm_retry_attempts = 3

[execute.ablation]

[models.code_task]
planner = "gpt-4o-mini"
writer = "gpt-4o-mini"
reviewer = "gpt-4o-mini"
editor = "gpt-4o-mini"
repair = "gpt-4o-mini"

[budget]
profile = "normal"
max_batches = 3
cost_cap_usd = 2.0

[budget.normal]
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000
```

`stream_benchmark_output` 取值：

| 值 | 含义 |
| --- | --- |
| `off` / `false` | 不实时转发 benchmark logs。 |
| `line` | 转发普通换行日志。 |
| `auto` / `true` | 同时处理普通日志和 `tqdm` 这类 carriage-return 进度输出。 |
| `summary` | benchmark 结束后只打印尾部摘要。 |

`baseline_policy` 取值：

| 值 | 含义 |
| --- | --- |
| `auto` | 默认行为；需要比较时运行未修改 baseline。 |
| `run` | 强制运行未修改 baseline。 |
| `skip` | 跳过未修改 baseline，但仍允许代码理解、review、validation 和最终 benchmark。 |
| `provided` | 从 `baseline_metrics_file` 记录用户已有指标；summary 会标注这些指标不是本次复测得到。 |
| `none` | 任务没有有意义的 baseline comparison，适合纯生成或验收式任务。 |

## Edit Scope Behavior

`[edit_scope]` 已经是 code-task 的真实安全契约，而不是只写给模型看的提示词。它会在多个位置重复生效：

- `init` 会把 `allowed_patterns`、`protected_patterns` 和可选 `mode` 写入 `code_task/manifest.json`；
- repo map、locate、context、work-plan、patch-plan、edit proposal 和 repair 会把 allowlist 之外的路径标为只读证据；
- `apply-edits` 写文件前还会再次校验 workspace-relative path、allowed patterns、protected patterns、当前 batch target files 和 old-text 精确匹配；
- 默认 protected patterns 始终保留，因此 tests、benchmark 文件、`.env`、secret/credential-like 路径等不会因为用户配置了 allowlist 而变成可写；
- `protected_patterns` 是在默认规则之外额外添加只读路径，不是替换默认规则。

常见配置如下：

```toml
[edit_scope]
# 允许模型改应用代码和必要的实验配置；测试、benchmark 与数据仍可作为 evidence 读取。
allowed_patterns = ["review_pipeline/**", "main.py", "configs/experiment.json"]

# 此示例不额外锁定配置；内置规则仍会保护 tests、数据、secret 和 credential-like 路径。
protected_patterns = []
```

如果 `allowed_patterns` 为空，含义是“允许所有非 protected 的 workspace 文件作为候选”。如果需要最严格的生产级流程，建议显式声明 allowlist，并把数据、配置、测试和 benchmark 放进 protected 范围。
