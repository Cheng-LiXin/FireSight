# 更新日志

本项目遵循 `v1.<次版本>.<修订号>` 的版本号约定：

- **次版本**（`v1.x.0`）—— 新增功能模块或有意义的能力扩展
- **修订号**（`v1.x.y`）—— 问题修复、文档更新、性能与小改动

版本号记录在 4 个 `_version.py` 中（引擎端、应用端与两个顶层能力包），由构建工具动态读取。

---

## [v1.5.0] — 2026-09-27

补齐 S5 规划的**告警事件**模块（筛选分页、片段回放、标记误报、导出 CSV）。
过程中挖出并修掉两个此前一直存在、但没人碰到的静默缺陷。

### 新增

**告警事件页面**（`apps/web/src/views/EventsView.vue`，替换原占位页）
- 筛选：关键词（消息 / 位置 / 事件号）、类别、状态（全部 / 待复核 / 已标误报）、日期范围
- 分页：每页 20 / 50 / 100，翻页不重不漏；筛选变化自动回到第一页
- 片段回放：点击展开 `<video>`，走「取景窗」容器
- 标记误报：就地复核；导出 CSV 交给浏览器原生下载
- 类别中文名从 `/api/system/status` 取，不在前端再抄一份契约
- 空态区分「一条都没有」与「当前筛选没匹配」——全库数量取自 system status

**REST 接口**（`apps/fire-sight/src/fire_sight/api/routers/events.py`）
- `GET /api/events` — 筛选 + 分页，返回 `{items, total, limit, offset}`
- `POST /api/events/{id}/false-positive` — 标记误报，幂等
- `GET /api/events/export` — 按当前筛选导出全部为 CSV（带 BOM，Excel 不乱码）
- `GET /api/events/{id}/clip` — 片段回放，限定事件目录内

### 修复

- **第二次推理会静默覆盖第一次的事件**。`event_id` 是 `evt-{seq}` 这样的**每次运行
  从 1 开始**的计数器，而它是事件库主键 + `INSERT OR REPLACE`——跑第二次就把第一次
  逐条改写成新时间，片段文件同名也被覆盖。事件历史因此被无声截断在一轮之内。
  改为 `AlertPolicy(id_prefix=...)`，`run_inference` 传入运行时间戳前缀。
  实测：连续两次推理，事件数 40 → **80**（修复前会停在 40），来自 2 个不同运行。
- **片段编码浏览器放不出来**。`write_clip` 用的是 `mp4v`（MPEG-4 Part 2），
  而浏览器只认 H.264 / VP8 / VP9 / AV1。实测 `<video>` 直接报
  `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`：src 正确、readyState 0、尺寸 0×0，
  界面上只剩一条控制栏。片段回放从上线起就是坏的。
  改为逐个候选编码试写（VP9/WebM → VP8 → avc1 → AV1），**每个都回读帧数验证**。
- **`write_clip` 会静默写出空容器**。本机缺 OpenH264 时 `writer.isOpened()` 照样返回
  `True`，不抛异常，却写出一个只有文件头、没有帧的 1001 字节 mp4。
  现在以「回读帧数 == 写入帧数」为准；全都不行就抛错，**`mp4v` 故意不作兜底**——
  浏览器播不了的片段比「没有片段」更糟：界面会给出回放按钮，用户点了永远没画面。
- **禁用态的 ghost 按钮看不出来**。`base.css` 只有 `.fs-btn-primary:disabled`，
  `.fs-btn-ghost` 没有。功能上确实 disabled（`toBeDisabled()` 通过），外观却和可点的
  一模一样——告警事件的「标为误报」与训练页的「停止中…」都受影响。
- **`fs-val` / `evaluate_weights` 与归档 `metrics.txt` 口径不一致**（评估路径修复，
  详见 v1.4.0 条目）。

### 变更

- **筛选从 Python 移到 SQL**（`EventStore._where` / `query_events` / `count_events`）。
  原实现是「取前 N 条再在 Python 里过滤」——分页会重复或漏记录，总数无从得知，
  筛稀有类别时还可能明明有匹配却返回空。
- 排序加 `event_id` 作为第二键。`ts` 只到秒，同一秒的多条没有稳定定序时分页会漂。
- **事件消息改用中文类别名**（「检测到明火、烟雾，位置：…」）。`labels` 字段仍是契约名。
- `label` / `status` 传未知值**显式返回 400**。不校验的话 `label=flame` 会安静地
  返回 0 条，看起来像「这段时间没有告警」。
- `mark_false_positive` 返回是否命中，事件不存在时 404；原实现无条件 UPDATE。
- 不外泄 `clip_path`：列表只给 `has_clip`，且以磁盘为准。

### 回归测试

- `tests/test_21_events_api.py`（24 条）：筛选各维度、分页不重不漏、参数校验 400、
  误报 404 与幂等、CSV 的 BOM 与 `\r\r\n` 检查、片段的越界与失效路径
- `tests/test_09_alerting.py`：片段写完后**回读帧数**，锁住「空容器」那类静默失败
- `apps/web/e2e/events.spec.ts`（8 条）：片段回放断言的是**解码结果**
  （readyState ≥ 2 且 videoWidth > 0），不是 src 字符串——mp4v 那版 src 完全正确

后端 117 → **142 passed**；前端 E2E 6 → **14 passed**（另 2 条按既有 skip 作用域跳过）。

### 未做

- 事件库的演示数据由**真实推理**生成（选 `data/processed/D-Fire/train` test 集里有标注的
  45 张图，`--cooldown 0 --buffer-frames 20`），两次运行共 80 条。
  **`cooldown=0` 是为凑够演示数据刻意关掉的**——告警冷却按墙钟计时，图片目录跑得远快于
  真实时间，保持默认 30 秒冷却几十张图只会响一两次。事件本身没有伪造。
- 片段目前只留 20 帧（0.8 秒），够验证回放，不够看火势发展过程。

---

## [v1.4.0] — 2026-09-27

把整套视觉方向从「热成像取景」换成「现代简约 · 灰白黑」，并重建了已经丢失的配色校验器。

这是界面层自 S5 开工以来最彻底的一次改动：**换的是明度关系与色彩政策，不是信息架构**——
三种容器、取景窗四角框标、等宽数字、风险尺全部保留。

### 变更

**色彩政策：界面无彩，彩色额度只留一个给告警**
- 基底 `#17130E/#221C15` 暖褐 → `#F7F8FA/#FFFFFF`；边框 `#332A20` → `#E5E7EB`
- 文字暖白 `#E9E1D4` → 近黑 `#111827`（16.69:1）；次要 `#8C8172` → `#4B5563`（7.11:1）
- `--fire-1/2/3` 重命名为 `--accent` / `--accent-hover` / `--accent-soft`
  —— 单色主题里继续叫 fire 就是撒谎。`--smoke-1..4` 与 `--fire-4` 一个引用点都没有，删除
- 新增 `--alarm: #D14A17`，**全界面唯一允许出现的彩色**，只表示「有火 / 有告警 / 出错」
- 风险尺从「冷→暖色相阶梯」改为「单色递深三档 + 最高档转告警红」
- 圆角 3px → 6px

**只做浅色**
- `index.html` 去掉 `class="dark"`，`color-scheme` 改 light
- 不再引入 `element-plus/theme-chalk/dark/css-vars.css`
- 令牌覆盖从 `html.dark` 移到 `:root`

**类别色：烟 = 深石板灰，火 = 那唯一的告警红**
- `#00A3C4/#D14A17` → `#1F2937/#D14A17`
- 白底实测：常视觉 ΔE 37.2 · deutan 36.3 · **protan 25.1** · 对比度 14.68 / 4.47
  —— 三条通道全部优于原来在暗底上验证过的基线（30.8 / 21.1）

**训练曲线 4 色：保留原色相，为白底重调**
- `#4C8FE0 #BE8219 #B85FA8 #4FA050` → `#236EC2 #B07B23 #941C84 #0B9D22`
- 对比度从 3.24–4.01 提到 3.58–7.61；相邻最差 deutan ΔE 从 13.0 提到 21.8

### 新增

**配色校验器重建**
- 原 `validate_palette.js` 已从开发机**完全丢失**（`.dsh`/`.claude`/`.codex`/`D:\Program`/`references`
  全域搜索无果），而文档一直写着「改色必须重跑校验器」——这条纪律实际上已经无法执行
- 按其记录的两组已知值反推出度量：**`ΔE = 100 × ‖OKLab(a) − OKLab(b)‖`，CVD 用 Machado 2009 severity 1.0**
- 自检精确复现：类别色 常视觉 30.8 / deutan 21.1（差 0.1）；训练板 相邻 13.0 / 21.6
- 顺带验证了注释里那句警告：把绿与琥珀排到相邻，deutan ΔE 确实掉到 5.9

### 修复

- **训练记录选中行在浅色下变成一条重灰带**：`TrainingView.vue` 里硬编码的
  `rgb(51 42 32 / 45%)`，改为 `--highlight` 令牌
- **识别页拖放区虚线边框**同样是硬编码的旧暖灰 `rgb(140 129 114 / 35%)`，改为 `--ink-4`
- **`fs-val` 的评估口径与归档 `metrics.txt` 不可比**（顺带发现）：

  | 路径 | conf | iou |
  |---|---|---|
  | 训练期验证（写进 `metrics.txt`） | `null` → ultralytics 兜底 **0.001** | **0.7** |
  | `fs-val` / `evaluate_weights` | `ValConfig` 默认 **0.25** | **0.6** |

  后果是**同一个权重给出两个 mAP**：归档记 0.7052，`fs-val` 只报 0.5831，差 0.12 全部
  来自阈值。ROADMAP 里计划的「模型库 · 多权重对比评估」正是调 `evaluate_weights`，
  上线后会直接暴露成「归档说 0.767，对比工具说 0.58」。

  已把 `ValConfig` 的 `conf`/`iou` 默认改为 **0.001 / 0.7**（mAP 计算口径），部署阈值
  仍归 `InferConfig`（0.25 / 0.6），两侧不再混用；`fs-val` CLI 默认同步；
  补了回归断言锁住这两个默认值。

  修复后实测：640 组模型在同一权重上读出 **0.7034**，与归档 **0.7052** 只差 0.0018
  （残差来自 batch 8 vs 40），差异从 0.122 收敛到 0.002。

### 过程中被数据推翻的两个判断

1. **「烟=浅灰 + 火=红」不成立**。`#6B7280` 与 `#D14A17` 明度几乎相同（0.551 vs 0.589），
   而灰与红在红绿色盲下主要靠**明度**分离，protan ΔE 因此只有 13.4——比原基线 21.1 还差。
   压到 `#1F2937` 后三条通道全部优于基线。这条已写进 `tokens.css` 注释防止后人改回去。
2. **灰阶撑不起 4 条曲线**。实测 `#FFFFFF` 上对比度 ≥3:1 的灰只有 5 档、相邻 ΔE 掉到 6.8
   （要 ≥13 才算可区分）。所以训练曲线保留彩色——但沿用原色相重调，分离度反而提高。

### 回归测试

- 前端 E2E 新增 `apps/web/e2e/vram-hint.spec.ts`（显存提示跟随设备切换）
- 后端新增评估口径回归断言；**117 passed**；`npm run build`（含 `vue-tsc`）通过

### 未做

- **完整 E2E 未在本轮跑**：`detect.spec.ts` 要跑 GPU 推理，与本轮同时进行的 P0-1 探针
  争显存，留到探针结束后补跑
- **暗色模式整块删除**，没有保留可切换项。如需恢复，`tokens.css` 的 `html.dark`
  段与 `palette.ts` 的暗底数值需要一并重建并重新校验

---

## [v1.3.2] — 2026-09-27

把会外溢到系统盘的缓存与临时文件全部收敛进工作区，并给训练加上显存预算守卫。

起因：点「开始训练」后 C 盘告急。查证结果是**训练本身没写 C 盘**（那次只往
`runs/training/` 写了 41 MB），C 盘一直是红的（可用 9.6%，低于资源管理器的
10% 阈值），真正吃盘的是 Docker Desktop 约 20 GB 与 `hiberfil.sys` 12.7 GB。
但顺着查下去，发现项目**确实**有几处往 C 盘漏，只是量小：

- `fs-val` 的 `model.val()` 没传 `project`，落到 ultralytics 全局 `runs_dir`
  ——本机那份 `%APPDATA%\Ultralytics\settings.json` 指向 C 盘，实测在那里
  留下了 `runs/detect/val` 与 `val-2` 共 24 个文件
- AMP 自检把 `yolo26n.pt` 下到 `C:\Users\<u>\weights`
- 训练任务配置走 `tempfile.gettempdir()`，落在 C 盘 Temp
- matplotlib 字体缓存、Arial.ttf 也都在用户目录

### 新增

**运行时目录 `RUNTIME_DIR`**（默认 `<工作区>/.runtime/`，`FIRESIGHT_RUNTIME_DIR` 可覆盖）
- `runtime_env.apply_runtime_env()`：在 **import ultralytics 之前**把
  `YOLO_CONFIG_DIR`、`TEMP`/`TMP`/`TMPDIR`、`MPLCONFIGDIR`、`TORCH_HOME`、
  `HF_HOME`、`XDG_CACHE_HOME` 全部改道进工作区
- `runtime_env.pin_ultralytics_paths()`：把 ultralytics 的 `datasets_dir` /
  `weights_dir` / `runs_dir` 钉进运行时目录下的那份 `settings.json`。
  **若当前生效的仍是用户的全局配置（`%APPDATA%`），拒绝写入并告警**——
  那是机器级设置，项目不该偷改
- 接入点：API 启动、训练子进程（含 `Popen(env=…)` 注入）、`train_yolo` /
  `evaluate_weights` / 两条推理路径

**显存预算守卫**（`fire_sight_platform/training/vram.py`）
- 峰值显存正比于 `batch × imgsz²`，用「像素单位」比较不同组合的开销
- 常数标定自本机唯一跑完且指标可信的现场：`batch=72 × imgsz=480`、
  RTX 4060 Laptop 8 GB、mAP@50 0.745
- `POST /api/train/jobs` 超预算返回 **400** 并给出上限与可照做的建议
  （`batch=48 × imgsz=640` → 「imgsz=640 时 batch 最多 40」），
  而不是把进程起起来等 CUDA OOM；CPU 或探测不到显卡时不拦
- 新增 `GET /api/train/limits?imgsz=&device=`，前端据此在表单上提前提示上限

### 修复

- **`fs-val` 的评估图件不再落到系统盘**：`model.val()` 补上
  `project=<运行时>/evaluation`、`name=<权重名>`、`exist_ok=True`。
  评估图件是过程副产物、没有消费方，因此不进 `runs/`
- **训练任务配置**从 `tempfile.gettempdir()`（Windows 上是 C 盘）改为
  `.runtime/tmp/`，并把改道后的环境变量显式注入训练子进程
- **总览页的推理路径**同样接入改道：`YOLO(weights)` 可能去下预训练权重，
  那会落到全局 `weights_dir`
- **停止训练后立刻再开始，会被判成「GPU 正被 train 占用」**（用户报告）。
  根因：`stop()` 只调了 `terminate()`，任务状态留给后台 `_reap` 去改，于是从点击
  停止到进程真正退出这段时间里 `running()` 仍认定任务在跑。改为**同步**把状态置为
  `stopping`，并新增 `stopped` 终态；停止期间再启动仍返回 409，但 `detail` 说明是
  「正在停止，几秒内释放」而不是含糊的占用提示。实测整个窗口约 **0.6 秒**
- **被主动停止的训练被记成「失败 · 退出码 1」**：Windows 上强杀返回退出码 1，
  旧代码按「退出码非零」判定为 `failed`。改为按停止意图判定为 `stopped`，
  不再留下 `error`
- **停止只杀直接子进程，DataLoader 的 workers 会变成孤儿继续占显存**。改为结束
  **整棵进程树**（Windows 走 `taskkill /F /T`），避免下一次训练因此 OOM
- **「停止中」的提示会跟着上一次查询的结果走**：`imgsz` 与 `device` 各自触发一次
  `/api/train/limits`，返回顺序不保证，慢的那次会覆盖快的结果——实测把设备切成
  CPU 后提示仍显示 GPU 的上限，`overBudget` 还会据此禁掉一个 CPU 下完全合法的
  配置。加请求序号守卫，只采纳最后一次发出的请求

### 回归测试

- `tests/test_18_runtime_paths.py`（6 条）：所有改道目标都在 `RUNTIME_DIR` 下；
  `apply_runtime_env` 既建目录又写 `os.environ`；**配置目录不在运行时目录下时
  拒绝改写全局配置**；任务配置确实落在 `.runtime/tmp/` 且子进程拿到新 `TEMP`
- `tests/test_19_vram_guard.py`（11 条）：标定点不会被自己的守卫拒掉
  （否则守卫比它要保护的东西还严格）；`batch=48 × imgsz=640` 被拒；
  广告出去的上限是**紧的**（多加 1 就超）；接口 400 与 `/limits` 行为
- `tests/test_20_job_stop.py`（11 条）：停止**同步**挪走状态；停止期间再启动给出
  可照做的 409；停止记成 `stopped` 而非 `failed`；真崩溃仍记 `failed`；锁确实
  归还；`stopping` 期间 SSE 不收流；Windows 走 `taskkill /T` 且有回退
- `apps/web/e2e/vram-hint.spec.ts`：切到 CPU 后显存提示收起、切回 GPU 恢复。
  单独一个文件，因为 `training.spec.ts` 的 `beforeEach` 会让整份文件被跳过

后端 88 → **116 passed**；前端 E2E 5 → **6 passed**（另有 2 条按设计跳过）。

### 变更

- `JobStatus` 新增 `stopping`；任务快照新增 `stop_requested_at`。
  接口契约同步更新到 `docs/api/REST-API.md` 与 `apps/web/src/api/types.ts`
- SSE 的收流判据从「状态 != running」改为「状态 ∈ 终态集合」，否则 `stopping`
  期间会提前收流，界面在进程树还没死的时候就显示「已停止」
- `.gitignore` 增加 `.runtime/`
- 版本号 v1.3.1 → **v1.3.2**（5 处：4 个 `_version.py` + `apps/web/package.json`）

### 未做

- **没有动 `C:\Users\<u>\runs` 与 `weights\yolo26n.pt` 这两处历史遗留**。
  它们在工作区之外，是否清理交给用户决定；改道之后新产物不会再进去
- **没有改 Windows 页面文件的位置**。实测页面文件峰值占用仅 0.61 GB
  （物理内存 31.7 GB），它跟这次磁盘压力无关，动它需要管理员权限加重启

---

## [v1.3.1] — 2026-09-26

引入前端端到端测试，并修掉它第一次跑就抓到的两个界面缺陷。

### 新增

**Playwright 端到端测试**（`apps/web/e2e/`）
- 驱动**系统已装的 Edge**，不下载 Playwright 自带浏览器
- 覆盖此前无法自动验证的路径：文件选择框投递、启动训练全流程
- 会真正写训练产物的用例默认跳过，按环境变量开启并自行清理

### 修复

- **权重列表就绪前上传图片会被静默丢弃**：页面挂载要去 `/api/models` 才知道该用
  哪个权重，期间 `runSingle()` 直接 `return`——没有请求、没有报错、没有提示，
  用户看到的是「图传上去了但永远没结果」。现改为**记住这次投递并补跑**，
  并在确实无模型时明确说明原因
- **实时训练结束后状态条永远停在「训练中」**：SSE 收尾事件的载荷是任务快照，
  **没有 `type` 字段**，而客户端按 `type` 分发，于是这条被静默丢弃。
  服务端补齐 `type: "close"`，协议恢复一致

### 回归测试

- 账册表头与数字列对齐。该缺陷本身已在此前的提交中修复，本次补上自动化断言
  （比对 `th.is-num` 与 `td.is-num` 的计算样式）——它此前只能靠人工看截图发现
- 结果对比图不拉伸变形、窄屏不塌

### 变更

- `.gitignore` 补 Playwright 的本地产物（`test-results/`、`playwright-report/`）

---

## [v1.3.0] — 2026-09-26

训练中心补齐「实时」部分：控制台能启动并观察训练了。

### 新增

**网页启动训练**
- 训练中心新增启动面板：数据集、模型、轮数、批量、imgsz、子集比例、设备
- 训练跑在**子进程**里——训练崩溃不会带走 API
- 实时曲线：逐轮损失（三种各一张小倍数图）与验证指标
- 实时日志：滚动展示最近 400 行
- 停止按钮

**REST 接口**
- `POST /api/train/jobs` —— 启动训练
- `GET /api/train/jobs` —— 任务列表（进程内状态，服务重启即清空）
- `GET /api/train/jobs/{id}/stream` —— SSE 推逐轮指标与日志
- `POST /api/train/jobs/{id}/stop` —— 停止训练

**引擎**
- `train_yolo` 新增可选的逐轮回调 `on_epoch`，命令行调用方行为不变
- `curves.extract_epoch_metrics`：把 ultralytics 的 trainer 对象收成与
  `results.csv` 同构的字典

### 变更

- 网页启动的训练与命令行启动走**完全相同的归档路径**，产物位置与格式一致
- 训练期间的 GPU 锁由任务管理器持有，**进程异常退出也会释放**
- 前端表单与按钮样式提到全局（`base.css`），识别页与训练中心共用，
  避免同一控件在两处各自演化

### 修复

- **训练损失静默消失**：ultralytics 的 `loss_items` 返回的是 dict 而非序列，
  按序列迭代 dict 拿到的是键名，`float()` 抛异常被容错的 `except` 吞掉，
  训练损失就此无声消失。两种形态现在都认
- **实时指标慢一轮**：`on_train_epoch_end` 触发时本轮验证尚未运行，改用它会让
  第一轮指标全是 0。改用 `on_fit_epoch_end`——ultralytics 写 `results.csv`
  正是在那一刻，逐值核对完全一致
- **实时曲线多出假点**：训练结束后的收尾验证会再次触发同一回调，曲线上多出
  一个 `epochs+1` 的点。现按 `epoch > epochs` 丢弃
- **断线重连会重放全部事件**：浏览器重连时发的是 `Last-Event-ID` 头而不是
  查询参数，只认后者会导致重复点。两个都认

### 已知限制

- 停止训练是强制结束进程，**本次训练不会归档**（现场记录仍保留在 `runs/`）
- 任务列表是进程内状态，服务重启即清空
- 控制台仍缺告警事件与模型库两个模块

---

## [v1.2.0] — 2026-09-26

训练中心（回看部分）：控制台能看训练过程了。

### 新增

**训练中心页**
- 训练记录账册：运行、数据集、轮数、mAP@50、是否归档；中断的训练也会列出，
  轮数就是实际跑到的地方
- 损失曲线：每种损失一张小倍数图，训练实线 / 验证虚线
- 验证指标曲线：mAP@50、mAP@50-95、精确率 P、召回率 R
- 现场图件：混淆矩阵、PR / F1 曲线、训练汇总图、标签分布、批次样例

**REST 接口**
- `GET /api/train/runs` —— 训练记录，新→旧
- `GET /api/train/curves?run_id=` —— 逐轮曲线（解析现场 `results.csv`）
- `GET /api/train/artifacts?run_id=` —— 现场图件清单
- `GET /api/train/artifact?run_id=&name=` —— 取单张图件

**引擎**
- `fire_sight_platform.training.curves`：`results.csv` 解析为可绘图序列的纯函数

### 变更

- 训练曲线使用独立的一组配色，不再借用类别色。青/橙已经表示烟雾与明火，
  挪来表示训练/验证会让同一个颜色在同一套界面里有两种含义。
  新色板经色觉障碍模拟校验（deutan ΔE 13.0、常视觉 ΔE 21.6）

### 已知限制

- 训练中心目前只做「回看」；网页启动训练与实时曲线、日志流是下一半
- 控制台仍缺训练中心实时部分、告警事件、模型库

---

## [v1.1.0] — 2026-09-26

新增识别检测模块：控制台第一次能真正跑推理。

### 新增

**识别检测页**
- 单张上传识别：拖放或点击选择，原图与检测结果并排对比
- 检测框账册：类别、置信度、四角坐标，按置信度降序
- 参数可调：权重、置信度阈值、IoU 阈值、CPU / GPU
- 目录批量：选一个已处理数据集的划分，输出逐图统计（张数、有检出、目标总数、平均耗时）

**REST 接口**
- `GET /api/models` —— 归档权重与指标，供权重下拉
- `GET /api/infer/sources` —— 可批量推理的目录
- `POST /api/infer/image` —— 单图识别，返回检测框与标注图
- `POST /api/infer/batch` —— 目录批量，逐图统计

### 修复

- **前端子页面的深链接与刷新全部白屏**：`StaticFiles(html=True)` 挂在根路径只服务
  真实存在的文件，而前端用 HTML5 history 模式。现改为静态资源挂 `/assets`、
  未命中路径交回 `index.html`；`/api/` 前缀仍返回 JSON 404
- **批量来源漏掉嵌套数据集目录**：`--dataset "D-Fire/train"` 的产物落在
  `processed/D-Fire/train/`，比顶层数据集多一层，只扫一层会让**主要数据集在
  批量来源里完全消失**。现以 `data.yaml` 为完整性标志、最多认两层
- **`run_id` 被从权重路径反推**：嵌套布局下 best.pt 的父目录是实验名
  （`train-yolo11n`），不是 `run_id`，前端无法据此对应模型列表。现由解析函数
  一并返回归档标识

### 变更

- `InferService` 支持 `iou` 与按次传入的 `conf`；批量默认跳过标注图渲染
- 模型按 `(weights, device)` 缓存复用。此前每次请求重新加载权重——
  实测**首次 1086 ms vs 后续 16–18 ms**，是可用与不可用的分界
- 推理前申请 GPU 全局锁，被训练占用时返回 409 与占用者信息，而非让 CUDA 报错

### 已知限制

- 控制台仍缺训练中心 / 告警事件 / 模型库三个模块
- 识别检测仅支持图像，视频文件推理未做
- 上传接口单张上限 20MB，批量单次上限 300 张

---

## [v1.0.0] — 2026-09-26

首个公开发布版本。包含从数据到监测的完整链路，以及 Vue 值班控制台。

### 新增

**控制台前端（全新）**
- Vue 3 + TypeScript + Element Plus + ECharts 构建的值班控制台
- 风险尺：以热成像色标编码当前风险，游标随数据变化
- 仪表盘：告警读数、按类别堆叠的告警趋势、平均置信度趋势、类别构成、当前模型指标
- 视觉体系：暖调炭褐底 + 冷端/热端双色标（类别决定色相家族，置信度决定家族内明度）
- 图表配色经色觉障碍模拟校验（deutan ΔE 21.1、常视觉 ΔE 30.8）

**REST 服务层（全新）**
- FastAPI 应用，生产模式直接托管打包后的前端
- `GET /api/system/status` —— 工作区、GPU、任务锁、类别契约
- `GET /api/overview` —— 仪表盘聚合数据
- GPU 全局互斥锁：8GB 显存下训练与推理串行化，避免现场显存溢出
- `fs-serve` 命令取代原 `fs-web`

**引擎端**（自 S0–S3 阶段积累，随本版本首次公开发布）
- Monorepo 骨架、可安装的双端包与工作区路径解析
- 数据工程：多格式转换（VOC / COCO / YOLO）、固定种子冻结划分、五项质检与 HTML 报告
- 训练评估：配置三源合并、YOLO11 训练、实验自动归档、多权重对照评估
- 推理告警：可插拔帧输入（摄像头 / 视频 / 图片夹 / RTSP）、多帧时序确认、
  中文语音播报、事件入库与片段留存

### 变更

- 数据服务层由 `fire_sight.web.services` 迁移至 `fire_sight.services`，
  与 HTTP 层解耦，供 REST 路由复用
- 数据库字段与前端展示统一使用 `smoke` / `fire` 命名

### 移除

- 旧版 Streamlit 界面（由 Vue 控制台取代），相关代码保留在 git 历史中

### 修复

- **demo 数据集生成器的类别名与契约相反**：旧版使用 `["flame", "smoke"]`，
  导致 VOC 路径报「未知类别」、YOLO 路径写出与契约相反的数字标签
- **带斜杠的数据集名会让训练归档多套一层目录**：`--dataset "D-Fire/train"` 产生的
  `run_id` 被直接拼进归档路径，得到 `trained/<run_id>/train-yolo11n/best.pt`，
  使该轮实验被按 `trained/*/best.pt` 查找的代码整轮漏掉。
  现 `make_run_id` 按 `naming` 规则统一清理，归档路径恒为扁平
- **`fs-transform` 无法让产物名独立于输入位置**：想换产物名只能改 `--dataset`，
  而改了 `--dataset` 原始数据就找不到了。现新增 `--name`（默认与 `--dataset` 相同）
- **`fs-transform` 接受任意 `--classes` 顺序**：对 Pascal VOC / COCO 输入，
  转换器按**名字**查表，错误顺序会把数字标签直接写反，而 `data.yaml` 仍按契约
  声明——数据被静默调换，模型照常训练、指标照常好看，事后几乎无法察觉。
  现强制要求 `smoke,fire`，否则在建目录之前即拒绝执行
- **最新训练轮次在接口层消失**：早期归档布局为 `trained/<run_id>/<实验名>/best.pt`，
  仅按 `trained/*/best.pt` 匹配会漏掉，使界面显示较旧的模型。现两种布局都认
- **仪表盘趋势图的 X 轴存在假间隔**：无告警的日期整条缺失，被坐标轴画成了正常间隔；
  现已按日补齐为 0
- **平均置信度把「无数据」画成 0**：无告警日的置信度应为未定义，现以断点表示而非折线归零

### 已知限制

- 控制台目前仅「仪表盘」可用，识别检测 / 训练中心 / 告警事件 / 模型库为占位页
- 训练启动与实时曲线尚未接入界面，暂时通过 `fs-train` 命令行执行
- 冻结划分按图片随机，不含分组感知。同一固定机位/同一场火的不同时刻会同时出现在
  训练集与测试集两侧，因此 **mAP 代表同机位性能，不能直接当作跨场景泛化能力**
  （详见 README「实测结果」）

---

*更早的开发过程（S0–S3 阶段）见 [`docs/devlog/`](docs/devlog/)。*
