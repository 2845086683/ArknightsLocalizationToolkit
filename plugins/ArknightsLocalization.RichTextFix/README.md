# 国服字体与富文本插件

## 功能概述

插件为日服、美服提供中文字体回退、富文本样式恢复及按控件用途区分的翻译。通过 XUnity 组件回调设置译文，不写入源文共享缓存，不改动全局 Text 词典；同一上下文仍有多个正式译文时跳过匹配。

### 字体与渲染

- 按实际字形覆盖选择字体。数字、英文及能完整显示中文的原字体和材质保持不变；缺字的中文文本使用国服字库。正文、Bold/Heavy/Black 标题及宋体分别使用对应字体，保留字号、加粗、描边和阴影。控件重新显示数字或英文时恢复原字体。
- 支持普通 UGUI、游戏 `UICommentedText` 以及 TMP UI/世界空间文本。字体切换在网格生成前完成，并使排版及字形缓存失效；字体检查有重入保护。启动时扫描已有场景组件，后续通过组件事件处理。
- UGUI 使用原生 FontEngine 字形索引检查覆盖，避免系统回退字体造成误判。TMP 保留显式 `<font=…>`、`<material=…>` 标签及原有材质效果。
- `UguiAtlasCache` 记录动态图集版本与完整排版参数，生成网格前清除过期的自定义顶点缓存，防止字形 UV 错位。字体重建通知先取得本轮文本快照，再刷新仍有效的控件，允许回调期间切换字体而不中断剩余文本刷新。

中文字体随插件提供，不需要安装到 Windows。`Resources/cn_heavy.bundle` 包含国服 `SourceHanSansCN-Heavy`，`Resources/cn_song.bundle` 包含国服 `方正特雅宋_GBK`；由 `tools/build_cn_display_font.py` 提取，宋体使用 `--role song`，相邻 JSON 记录来源和哈希。干员详情的 `panel_illustration_name/label_realname` 使用特雅宋同类回退，保留详情大名的设计。

`Resources/cn_tmp_shader.bundle` 提供标准 `TextMeshPro/Mobile/Distance Field` Shader，不包含字体或字形。它来自 XUnity 的 `TMP_Font_AssetBundles_2025-12-08.7z` 内 Unity 2021 资源，可用 `tools/build_tmp_shader.py` 从哈希锁定的缓存重新提取。

### 富文本样式

`RichTextStyleRestorer` 在整段翻译后，根据整段包装标签、精确译词及无歧义数值恢复颜色、字号、加粗等样式。`OfficialRichStyles` 提供完整中文样式目标与官方宏定义，用于技能数值、关键词和提示语高亮；即使源文本的宏已被剥离，也能匹配完整中文目标。

官方样式只在可见文本与已选译文完全一致时应用。不能确定对应位置或存在样式冲突的目标会跳过，不按字符比例猜测。更新样式数据可运行 `tools/build_rich_styles.py`，然后重新编译插件。

### 上下文翻译

`ContextTranslations` 和 `tools/context_domains.py` 按官方稳定 ID 配对数据，生成嵌入式字段词库及有类型的动态模板。`ComponentFields` 要求原生字段确实指向当前文本，只缓存反射元数据，每次检查当前父级和字段引用；支持已登记的原生 `Text[]` 字段、控件复用和等待文本稳定期间的翻译。

- **文本 ID 与精英化信息**：同时匹配 `Text.textId` 和对应源文，区分设置页 Key → 按键与其他位置的含义。精英化摘要和详情使用各自官方模板，保留技能、天赋名称及数值的颜色。
- **干员名称**：卡片、选择列表和详情使用 `operator-character`，仅收录 `char_` 行，区分 Mountain → 山与地图物件“山脉”，也支持 Flint → 燧石、Mint → 薄绿、Vigil → 伺夜及大写别名。详情大名按完整已知层级识别。通用战斗名称保留独立查询范围。
- **卫戍协议**：通过活动 `charId` / `backupCharId` 关联官方名称，覆盖战斗顶部、盟约详情、商店、选人列表及局内干员详情的 Precision、Solo、Elite 等词条。`operator-autochess` 区分预备干员与同名正式干员；Sharp、Touch、Mechanist 等国服英文代号保持原文。
- **职业、属性与标签**：区分职业分支 Executor → 处决者与干员 Executor → 送葬人。攻击速度、再部署速度和干员标签使用独立词库，Slow 在攻击速度中为“慢”、标签中为“减速”。支持完整标签组合，保留分隔符及富文本包装。
- **基建、公招与任务**：覆盖基建房间和技能、公招词条、特勤任务未解锁提示、玩法路径及特殊干员任务。动态玩法参数必须匹配正式模式或主题名称。
- **剧情与玩家身份**：剧情按同文件、相同指令结构对齐，支持颜色、斜体及 `{@nickname}`。卫戍协议播报区分玩家昵称、干员名和数值；玩家名即使叫 Gnosis 也保留，干员参数则译为灵知。独立昵称、玩家编号、别名及专用英文代号字段不翻译。

构建上下文词库使用 `python tools/build_context_translations.py`。旁边的报告列出被排除的歧义词；不使用模糊剧情对齐或按出现频率猜译。

## 构建与分发

在项目根目录构建，需要 .NET SDK 和已初始化的客户端 interop：

```powershell
dotnet build plugins/ArknightsLocalization.RichTextFix -c Release -p:GameInteropPath=C:/YostarGames/Arknights_JP/BepInEx/interop -p:BaseIntermediateOutputPath="$PWD/work/dotnet/ArknightsLocalization.RichTextFix/obj/"
```

输出为 `tools/runtime/ArknightsLocalization.RichTextFix.dll`。发布时同步 `runtime` 下双服官方词库中的 DLL，并更新各自清单的 `rich_text_plugin.sha256` 及 `files` 中的 DLL 大小和哈希。

## 验证

```powershell
.conda-env/python.exe tests/run_font_smoke.py C:/YostarGames/Arknights_JP
.conda-env/python.exe tests/run_font_smoke.py C:/YostarGames/Arknights_EN
.conda-env/python.exe -m pytest tests/test_fonts.py tests/test_style_policy.py -q
```

原生检查需要已安装汉化且已退出的客户端。脚本临时部署测试插件，验证字体、材质、真实技能高亮、字段消歧、玩家身份、控件复用和网格缓存。图集压力测试比较实际提交的 UV 与全新排版结果，并覆盖字体重建回调中的字体切换。检查后游戏自动退出，脚本恢复原插件和配置，移除测试插件；日志保存在 `work/font-smoke-*/font-smoke.log`。

纯策略测试直接编译执行生产 C# 实现，覆盖语序改变、重复数值、嵌套标签、未闭合标签、noparse、链接、官方宏及两服真实上下文样本。原生组件测试不能替代每个业务界面的视觉验收。

## 适配范围

普通 UGUI 同一组件只能使用一套基础字体；混合“中文说明＋数字”的组件若缺字，会一起切换字体。独立数字组件与 TMP 显式数字字体片段保留原样。正文、Heavy 和特雅宋有对应国服字库，其他特殊美术字体缺字时使用正文或 Heavy 回退。无法从页面、文本 ID、字段类型或官方样式表确定的译文和局部样式会跳过。
