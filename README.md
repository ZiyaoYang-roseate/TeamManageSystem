# NBA 球队管理系统

Flask 和 Jinja 提供管理页面与 API，MySQL 保存球队、球员、合同、交易和比赛数据。

## 本地启动

需要 Python 3.12 和 MySQL 8.0。以下命令在项目目录的 PowerShell 7 中执行。已有可用虚拟环境时，可以直接将 `$python` 指向其解释器。

```powershell
py -3.12 -m venv .venv-windows
$python = ".\.venv-windows\Scripts\python.exe"
& $python -m pip install -r requirements.txt
$env:MYSQL_DATABASE = "nba_management_db"
$env:MYSQL_USER = Read-Host "MySQL 用户名"
$env:MYSQL_PASSWORD = Read-Host "MySQL 密码" -MaskInput
$env:FLASK_SECRET_KEY = & $python -c "import secrets; print(secrets.token_hex(32))"
```

默认连接 `127.0.0.1:3306`。用 `MYSQL_HOST` 和 `MYSQL_PORT` 覆盖。多次启动需沿用同一个会话密钥，轮换密钥后用户需要重新登录。使用 HTTPS 部署时将 `SESSION_COOKIE_SECURE` 设为 `1`。

## 创建或升级数据库

对已有数据库，先备份并停止提供写入服务，再用有结构迁移权限的 MySQL 用户运行以下命令。迁移记录保存在 `schema_migrations`。

```powershell
& $python migrate_db.py
```

新库可运行 `& $python migrate_db.py --create-database`。迁移后的原有比赛、合同和交易记录继续保留。历史招募目标和报价缺少归属，保留 `team_id IS NULL` 的原始记录，并在球队看板中隐藏。确认历史目标归属后，可在维护窗口由数据库管理员逐条补上球队 ID。

完成迁移后，为日常运行设置仅有必要读写权限的数据库账号，再启动应用。

```powershell
.\active.ps1 -Python $python
```

在浏览器打开 `http://127.0.0.1:5000`。普通启动只运行 Web 应用。旧版 `*_migration.sql` 文件保留用于查看版本历史，升级使用 `migrate_db.py`。

## 账号

应用提供联赛管理员、球队管理层和记分员三种登录身份。联赛管理员进入联赛运营台，查看首发待办、进行中的比赛、复盘待办和完赛时钟核查，并维护全局球队与球员档案。球队管理层可编辑本队资料，并通过自由市场、签约、裁员和交易审批管理阵容。记分员维护比赛日和比赛数据。

```powershell
& $python manage_users.py league_admin --role league_admin
& $python manage_users.py example_gm --role management --team 1
& $python manage_users.py scorer --role scorekeeper
& $python manage_users.py scorer --reset-password
```

根据实际球队 ID 创建管理账号。密码由命令交互输入，至少 12 个字符。旧账号保留，旧版短演示密码已停用，可用 `--reset-password` 更新原账号。此前写进启动脚本的 MySQL 密码需在 MySQL 中轮换。

## 演示库与测试

演示库重建会删除指定库中的项目业务表。命令仅接受以 `_demo` 结尾的数据库名和显式重置参数。

```powershell
$env:MYSQL_DATABASE = "nba_management_demo"
& $python init_db.py --reset-demo
& $python -B -m unittest discover -s tests -v
```

自动化回归测试可以在没有 MySQL 连接的环境运行。签约锁、交易回滚和计分幂等的联调使用隔离 MySQL 库。

## 联赛赛前分析、赛后复盘与球队决策

迁移版本 3 增加 `game_analysis_snapshots` 和 `game_analysis_reports`。如果运行账号使用按表授权，需要给它这两张表的 `SELECT`、`INSERT`、`UPDATE` 权限。

赛前分析属于球队备赛资料。球队管理层只能访问本队参赛的比赛和本队视角，网页、生成接口、证据接口和历史报告链接使用同一权限。联赛管理员可以切换双方视角。赛前分析可在任一比赛状态查看或生成，使用比赛日期之前的完赛数据，结合对手的出手区域、重点球员和本场出勤公告提供对位、防守与轮换预案。当前伤病表只作为待开赛比赛的参考。赛后复盘全联盟管理层共享，依据双方技术统计、关键时段和出手区域生成下一场的训练与验证建议。

迁移版本 4 新增 `game_player_availability` 和 `game_starters`。按表授权时，运行账号需要两张表的 `SELECT`、`INSERT`、`UPDATE`、`DELETE` 权限。球队管理层在赛前分析里提交本队的出勤状态和首发五人，联赛管理员可以代为更新。双方首发就绪后，记分员开赛时自动载入，比赛进行中继续记录换人。迁移版本 5 增加首发确认状态。升级前已开打但缺首发的比赛需由双方补交名单，再由记分员确认后继续，已有比分、时钟和比赛记录保留。

招募和交易分析只对当前球队管理层开放。签约比较候选与本队同位置球员，交易对比换入和换出球员及交易后的阵容覆盖。两处都结合本队与同期对手的篮板、助攻、三分和失误差距，展示候选球员每 36 分钟的技术特点和样本规模，同时保留薪资和规则校验。样本不足时不量化补强效果。两处的「智能辅助」可从全市场生成最多三套方案，交易暂按一换一搜索，签约使用一年固定工资。球队管理层应用选定方案时须确认，提交仍通过原交易和签约规则重新校验。外部模型仅收到泛化的因素和行动选项。

如果用本机的 `start-local.ps1` 启动，运行 `configure_deepseek.ps1` 可验证 API Key 并保存到 Windows 加密配置。重启项目后生效。默认使用 `deepseek-flash`。

也可以在启动 Web 服务的 PowerShell 会话中设置 DeepSeek 配置。

```powershell
$env:NBA_AI_PROVIDER = "deepseek"
$env:NBA_AI_MODEL = "deepseek-flash"
$env:DEEPSEEK_API_KEY = Read-Host "DeepSeek API Key" -MaskInput
```

如果使用 OpenAI，设置 `NBA_AI_PROVIDER=openai`，并选择支持 Responses API JSON Schema 的模型。

```powershell
$env:OPENAI_API_KEY = Read-Host "OpenAI API Key" -MaskInput
$env:NBA_AI_MODEL = Read-Host "支持结构化输出的模型 ID"
```

只有在配置模型且用户点击生成时，应用才发送筛选后的比赛事实和候选结论。模型选择重点与后续行动，统计值来自比赛快照。未配置模型或调用失败时，页面显示规则报告。报告历史按球队保留，同一证据版本与配置复用既有报告。规则兜底的报告可在页面重试模型生成。

演示时先发布双方首发，再用记分员进入当日计分面板验证自动载入。非参赛球队可以读赛后复盘，也可验证其无法查看赛前报告、修改首发或出勤公告，或分析其他球队的私有招募目标。

迁移版本 6 增加报价版本、合同年涨幅和事件更正记录。正式签约按已确认的报价保存条款，并按所选赛季计算递增后的薪资。旧报价缺少赛季时可以参考原条款，须在选定赛季重新保存后签约。智能辅助的固定年薪签约在同一事务中保存对应报价。球队选项和球员选项随合同保存，目前没有执行或放弃选项的操作。

正常完赛要求第四节或当前加时赛时钟归零、且比分分出胜负。比赛进行中可撤销新版录入的最新有效事件，原事件与更正原因仍显示在逐回合日志。运行账号需有 `play_by_play`、`shot_attempts` 的 `UPDATE` 权限。
