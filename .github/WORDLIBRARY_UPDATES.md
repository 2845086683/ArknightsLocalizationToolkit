# GitHub Actions 自动更新词库

工作流：[`Update wordlibraries`](https://github.com/2845086683/ArknightsLocalizationToolkit/actions/workflows/update-wordlibraries.yml)。配置文件为 `.github/workflows/update-wordlibraries.yml`。

## 更新方式

- 每 6 小时检查一次，计划北京时间 **02:17、08:17、14:17、20:17**。GitHub 调度可能延迟。
- 跟踪现有数据源 [ArknightsAssets/ArknightsGamedata](https://github.com/ArknightsAssets/ArknightsGamedata/tree/master) 的 `master` 分支。其中 `en` 为美服、`jp` 为日服、`cn` 为国服；三服在同一仓库，无需另外配置三个仓库。
- 通过 GitHub API 比较当前区服和国服目录的 Git tree SHA；对应区服、国服或构建代码变化才下载数据。EN 和 JP 各自记录状态，另一外服更新不会触发本服重建。首次运行各自构建。
- 每个区服固定自己的上游提交 SHA，下载本服与国服数据，然后复用现有稳定键、剧情结构对齐和冲突过滤逻辑，生成本服 UI、数据表、剧情、格式匹配词表。无需游戏客户端、Conda、API Key 或第三方 Python 包。
- EN、JP 使用两个独立任务，分别验证、分别直接推送 `main`，不创建 PR。某服失败不会取消另一服。每次只更新本服 `runtime/{en|jp}-zh-offline-final` 下的四份翻译文本、`ARKLOCALIZER_MANIFEST.json` 和 `.github/wordlibrary-state/{en|jp}.json`。启动器的现有 Git 词库更新功能即可获取结果。
- 版本使用上游各服 `hot_update_list.json` 的 `versionId`（游戏资源版本，不冒充 PC 客户端安装包版本）。提交标题及运行摘要标明 EN/JP 和资源版本；清单的 `wordlibrary_build` 与状态文件同时记录本服、CN 资源版本、上游提交 SHA 和构建器哈希。
- 保留运行时插件、字体、配置、`WORDLIBRARY.json` 身份和清单中非翻译文件的元数据。仅使用公开数据，覆盖率受上游同步进度影响，不能代替本地客户端提取。
- `15_format_regex.txt` 中已验证的客户端格式匹配规则会在公开数据未生成同名规则时保留；新生成的同名规则优先。构建报告的 `retained_client_format_rules` 记录保留数量，普通文本仍从最新数据重建。

## 启用与手动运行

1. 将本次新增的工作流、`arklocalizer/auto_update.py` 和 `tests/test_auto_update.py` 提交到远端 `main`。代码推送会启动首次构建。
2. 仓库 Actions 必须启用，并允许使用官方 `actions/checkout`、`actions/setup-python`、`actions/upload-artifact`。工作流声明 `contents: write`，默认使用 GitHub 自动提供的 `GITHUB_TOKEN`。
3. 当前 `main` 要求 PR，但维护者账号有 bypass。账号的 bypass 不会自动传给 `github-actions[bot]`。若默认令牌被保护规则拒绝，在仓库 Settings → Secrets and variables → Actions 添加 `WORDLIBRARY_PUSH_TOKEN`，使用被允许 bypass 的账号或 GitHub App 的令牌，并授予本仓库 Contents: Read and write。工作流优先使用该 Secret，保持直接推送；不会删除保护规则或强推。
4. 手动更新：打开上述工作流，点击 **Run workflow**，分支选择 `main`，`locale` 选择 `en`、`jp` 或 `all`。`all` 仍分别构建与发布，不等待双服同步。勾选 `force` 可以在上游无变化时重新构建，但不会跳过质量校验。

GitHub 对长时间不活跃的公开仓库可能暂停定时任务（[官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)，60 天无仓库活动）；可在 Actions 页面重新启用。Fork 默认不自动提交，如要在 Fork 运行，应修改工作流的仓库名称限制及项目内官方词库来源配置。

## 验证与失败处理

任何一项失败都不会推送结果或推进远端构建状态：

- 上游提交或目录哈希不匹配、工作树有修改、当前区服或 CN 必要数据目录/资源版本缺失。
- 现有词库与清单的大小或 SHA-256 不一致。
- 新词库格式错误、同源文本冲突、预期文件缺失或额外文件出现。
- UI、数据表、剧情任一类别为空，或任一文件词条数量比现有词库减少超过 10%（格式匹配文件允许原本为空）。该阈值用于拦截上游不完整数据；实际大规模删减需要维护者核对后更新基线或调整代码。
- 构建期间本服词库或构建代码发生修改，或推送被分支保护拒绝。另一服先发布时，自动 rebase 后重试普通推送（最多 3 次）；不会互相覆盖。

运行摘要显示本服与 CN 资源版本、上游提交、本服词条数量和排除的冲突数量。`wordlibrary-reports-{en|jp}-*` artifact 保存构建计划、格式校验、对齐统计、冲突及占位符报告 14 天。无变化时不生成空提交；输入版本变化但生成文本一致时，只提交来源状态，避免重复下载和构建。

## 本地复现

在项目根目录使用 Python 3.12：

```powershell
python -m unittest discover -s tests -p test_auto_update.py -v
python -m arklocalizer.auto_update check --locale en
```

将 `cache/ArknightsGamedataMulti` 检出到 `work/wordlibrary-update/en/build-plan.json` 中的上游 `commit`，确保 `en`、`cn` 的 `hot_update_list.json`、`i18n`、`gamedata/excel`、`gamedata/story` 都已下载，再执行：

```powershell
python -m arklocalizer.auto_update build --locale en
```

日服将 `en` 改成 `jp`。`build` 仅更新所选区服的本地官方词库及状态文件，但不会自行执行 Git 提交或推送。如需保留工作区内尚未提交的词库修改，请在独立 checkout/worktree 中运行。无需执行下载全部运行时组件的 `prepare-components` 或 `update-data`。
