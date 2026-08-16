# 明日方舟 PC 日服／美服离线中文工具

> 项目作者B站主页：[繁花掠影](https://space.bilibili.com/13552115)
> 相关视频教程[点击此处查看](https://www.bilibili.com/video/BV1sXb96BEnB/)
> 
> 重要！使用前请完整阅读“使用声明与风险”一节。

## 项目简介

本工具用于将《明日方舟》Windows PC 日服（JP）或美服（EN）的游戏内文本映射为简体中文。

运行游戏时不调用翻译 API，不需要翻译 API Key，也不包含在线翻译器 DLL。正式发布包已经内置日服、美服运行时和完整词库，安装与卸载过程不需要联网、Conda 或外部 Python。

当前随包提供的正式词表：

| 区域    | UI/i18n |  数据表 |    剧情 |    合计 | 碰撞源（已排除） | 严格格式错误 |
| ------- | ------: | ------: | ------: | ------: | ---------------: | -----------: |
| EN → zh |   2,324 | 107,379 | 337,447 | 447,150 |            6,667 |            0 |
| JP → zh |   2,250 | 111,396 | 350,031 | 463,677 |            5,481 |            0 |

## 系统要求

普通用户只安装内置词库时：

- Windows 10/11 64 位。
- 已安装并已下载完整游戏资源的最新明日方舟 Windows PC 美服或日服客户端。
- 安装、修复或卸载前必须彻底退出 `Arknights.exe`。
- 解压完整发布包；不要只复制启动器 EXE，旁边的 `outputs\runtime` 是内置双服词库和运行时。

维护者更新并生成词库时还需要：

- Git、Anaconda 或 Miniconda，以及可访问所列公开仓库的网络。
- 数 GB 临时磁盘空间；构建环境默认保存在项目内的 `.conda-env`，缓存和检出保存在 `cache`、`vendor`。
- 完整发布包或源码目录。

## 快速使用

### 1. 启动工具

双击：

```text
ArknightsLocalizationToolkit.exe
```

发布包不附带普通用户用不到的 PowerShell 构建、打包或维护脚本。

### 2. 选择客户端

点击“浏览”，选择对应客户端目录中的 `Arknights.exe`，例如：

```text
C:\YostarGames\Arknights_EN\Arknights.exe
C:\YostarGames\Arknights_JP\Arknights.exe
```

启动器通常会根据路径自动识别美服或日服；识别失败时可手动选择 `EN` 或 `JP`。

### 3. 扫描并安装

1. 确认游戏已经完全退出。
2. 点击“扫描客户端”，确认状态为“原版客户端 / 待安装”。
3. 若直接使用随包词表，点击“安装 / 修复并启动”。
4. 阅读确认信息后继续；工具会校验运行时、备份冲突文件、安装汉化并启动游戏。

本工具采用关闭游戏后部署 UnityDoorstop/BepInEx/XUnity 文件的持久加载方式。首次成功安装后，直接运行原来的 `Arknights.exe` 也会加载汉化；仍建议从本启动器进入，以便及时发现文件缺失或被游戏更新覆盖。

## 按钮说明

| 按钮 | 作用 |
| --- | --- |
| 初始化构建环境 | 创建项目内 Python 3.12 环境，安装依赖，下载并校验固定版本组件，克隆/更新公开数据和 Schema。 |
| 扫描客户端 | 读取资源数量、游戏进程和汉化状态，不修改游戏文件。 |
| 更新词表并重建 | 从所选客户端提取数据表和剧情，结合公开数据重建、校验词库，并封装新的离线运行时。 |
| 安装 / 修复并启动 | 校验并持久安装当前区域运行时，然后启动游戏。 |
| 仅启动游戏 | 不修改文件，只启动已经选定的客户端。 |
| 卸载汉化 | 按安装清单恢复备份，并移除仍与本工具哈希一致的新增文件。 |

## 后续版本更新词库

### 图形界面（推荐）

1. 完全退出 `Arknights.exe`，在启动器中选择要更新的美服或日服客户端，并确认区域为 `EN` 或 `JP`。
2. 如果访问 GitHub 需要代理，在“HTTP(S) 代理”中填写例如 `http://127.0.0.1:7890`；不需要时留空。
3. 第一次维护时点击“初始化构建环境”。工具会在项目内创建 `.conda-env`、安装 `requirements.txt`、下载并校验固定版本 BepInEx/XUnity/字体，并初始化公开数据、Schema 和解析参考仓库。
4. 保持“重建前更新公开仓库”勾选，点击“更新词表并重建”。工具会提取当前客户端的数据表和剧情，对齐中文公开数据，过滤冲突，严格校验词表并封装运行时。
5. 成功结果位于 `outputs\builds\区域-时间\`，其中包含 `client-tables`、`client-story`、`pack`、`pack-validation.json` 和 `runtime`。启动器同时更新 `outputs\current-en-runtime.txt` 或 `outputs\current-jp-runtime.txt`，后续“安装 / 修复并启动”会优先使用这次生成的运行时。
6. 关闭游戏后点击“安装 / 修复并启动”，实机检查新增干员信息、剧情内容、活动界面等。另一服务器需要切换区域和客户端后分别重建。

若只想使用随包正式词库，不需要执行以上初始化和重建步骤。

### 命令行完整流程

以下命令在项目根目录的 PowerShell 中执行。示例为美服；日服将 `$Locale` 改为 `jp`，并修改 `$GameDir`。

首次初始化：

```powershell
conda create --yes --prefix .conda-env python=3.12
.\.conda-env\python.exe -m pip install --requirement requirements.txt
.\.conda-env\python.exe -m arklocalizer.cli prepare-components
.\.conda-env\python.exe -m arklocalizer.cli update-data
.\.conda-env\python.exe -m arklocalizer.cli doctor
```

需要代理时，在 `prepare-components` 和 `update-data` 后追加 `--proxy http://127.0.0.1:7890`。后续每次客户端或公开数据更新后执行：

```powershell
$Project = (Get-Location).Path
$Python = Join-Path $Project ".conda-env\python.exe"
$Locale = "en"
$GameDir = "C:\YostarGames\Arknights_EN"
$BuildRoot = Join-Path $Project ("outputs\builds\{0}-{1}" -f $Locale, (Get-Date -Format "yyyyMMdd-HHmmss"))

& $Python -m arklocalizer.cli update-data
& $Python -m arklocalizer.cli extract-client --game-dir $GameDir --scope tables --output (Join-Path $BuildRoot "client-tables")
& $Python -m arklocalizer.cli extract-client --game-dir $GameDir --scope story --output (Join-Path $BuildRoot "client-story")
& $Python -m arklocalizer.cli build-pack --locale $Locale --local-source-root (Join-Path $BuildRoot "client-tables\decoded\dyn") --local-story-root (Join-Path $BuildRoot "client-story\decoded\dyn") --output (Join-Path $BuildRoot "pack")
& $Python -m arklocalizer.cli validate-pack --pack (Join-Path $BuildRoot "pack") --report (Join-Path $BuildRoot "pack-validation.json")
& $Python -m arklocalizer.cli stage-runtime --locale $Locale --pack (Join-Path $BuildRoot "pack") --font (Join-Path $Project "cache\fonts\arialuni_sdf_u2021") --output (Join-Path $BuildRoot "runtime")
```

确认 `pack-validation.json` 中 `ok` 为 `true` 后，可直接安装本次结果：

```powershell
& $Python -m arklocalizer.cli install --stage (Join-Path $BuildRoot "runtime") --game-dir $GameDir --apply --summary
```

如果只需要根据最新公开仓库重建词库，可省略两条 `extract-client` 命令，并从 `build-pack` 中移除 `--local-source-root`、`--local-story-root`；不过游戏刚更新时，保留客户端提取结果能覆盖尚未进入公开仓库的新文本。

## 游戏更新后的处理

小型更新后，可以先关闭游戏，点击“扫描客户端”。

- 状态仍为“离线汉化已安装”：可以正常启动。
- 状态为“汉化需要修复”：点击“安装 / 修复并启动”。
- 客户端文本或活动内容已明显更新：获取与新版客户端匹配的新发布包，再执行修复。
- 状态为“检测到外部注入”：不要混装两套加载器。建议先通过客户端校验/重装恢复原版，再使用本工具。

## 卸载与恢复

1. 完全退出游戏。
2. 在启动器中选择原来的 `Arknights.exe`。
3. 点击“卸载汉化”并确认。

安装时被替换的文件备份在游戏目录：

```text
.arklocalizer-backup\安装时间戳
```

安装记录为：

```text
ArknightsLocalizationToolkit.install.json
```

卸载器只会删除哈希仍与本工具安装版本一致的文件。如果文件在安装后被其他程序修改，会标记为 `skip_modified` 并保留，避免误删。

## 配置文件和环境变量

图形启动器配置和安装/卸载报告默认保存在：

```text
%LOCALAPPDATA%\ArknightsLocalizationToolkit\launcher.json
%LOCALAPPDATA%\ArknightsLocalizationToolkit\reports\
```

支持以下环境变量：

| 环境变量 | 用途 |
| --- | --- |
| `ARKLOCALIZER_HOME` | 工具根目录。 |
| `ARKLOCALIZER_CONFIG` | 自定义启动器配置文件路径。 |
| `ARKLOCALIZER_GAME_EXE` | `Arknights.exe` 完整路径。 |
| `ARKLOCALIZER_LOCALE` | 区域，值为 `en` 或 `jp`。 |
| `ARKLOCALIZER_PROXY` | 构建时使用的 HTTP(S) 代理，例如 `http://127.0.0.1:7890`。 |

## 使用的第三方项目

感谢以下开源项目和公开数据仓库。本工具只在对应功能范围内使用或参考它们；各项目的版权和许可仍归原作者及贡献者所有。

| 项目 | 本工具中的用途 |
| --- | --- |
| [Ark-Unpacker](https://github.com/isHarryh/Ark-Unpacker) | LZ4AK、PC 客户端路径和动态数据解析参考。 |
| [ArknightsFlatbuffers](https://github.com/ArknightsAssets/ArknightsFlatbuffers) | Yostar/CN/TW 动态表 FlatBuffers Schema。 |
| [ArknightsGamedata](https://github.com/ArknightsAssets/ArknightsGamedata) | EN/JP/CN 公开数据，用于稳定键和中文目标文本对齐。 |
| [BepInEx](https://github.com/BepInEx/BepInEx) | Unity IL2CPP 插件加载运行时。 |
| [XUnity.AutoTranslator](https://github.com/bbepis/XUnity.AutoTranslator) | Unity UGUI/TextMeshPro 文本挂接和离线静态词表加载。 |
| [FlatBuffers](https://github.com/google/flatbuffers) | 使用 `flatc.exe` 把二进制表转换为 JSON。 |

## 使用声明与风险

本项目与上海鹰角网络科技有限公司、Hypergryph、Yostar、Yostar Limited、Yostar Games、《明日方舟》运营方及其关联公司没有隶属、授权、赞助或背书关系。《明日方舟》的名称、商标、文本、美术、音频、字体替代前的原始资源以及其他游戏资产归其各自权利人所有。

本工具面向用户本人合法安装的 Windows 客户端，用于个人研究、本地化和可访问性用途。请勿将提取出的完整游戏资源、受版权保护文本或包含其内容的衍生包用于商业用途或未经许可的大规模公开再分发。公开数据仓库没有独立许可文件的部分不应被默认视为可自由再分发。使用者有责任确认所在地区法律、游戏最终用户协议和第三方项目许可证所允许的范围。

BepInEx、UnityDoorstop 和 XUnity 会在游戏进程中加载第三方代码。某次可以正常登录、或反作弊没有立即拒绝，不代表未来客户端版本、服务条款或账号层面永远安全。本工具不提供反作弊绕过、规避检测、隐藏加载器、DRM 绕过、服务器限制绕过或联网协议修改功能。是否使用以及由此产生的账号风险由使用者自行判断和承担。

本工具按“现状”提供，不保证翻译覆盖率、游戏更新后的兼容性、账号安全或持续可用性。请在使用前备份重要文件，并保留通过官方启动器校验或重新下载客户端的能力。作者及贡献者不对账号处罚、存档或文件损坏、软件冲突以及任何直接或间接损失承担保证责任，但本工具仍通过哈希清单、运行中保护和可回滚备份尽量降低本地文件风险。
