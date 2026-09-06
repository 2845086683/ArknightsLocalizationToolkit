# 国服字体与富文本插件

版本 1.9.0 按实际字形覆盖决定是否换字体，取代 1.2.0 的强制统一字体策略。UGUI 在 `OnPopulateMesh` / `UpdateGeometry` 之前应用字体，另覆盖 `GetGenerationSettings` 布局查询和游戏 `Torappu.UI.UICommentedText`。换字体时使排版与字形缓存失效；对字体检查全过程防重入，避免 XUnity 同步回调打乱原字体记录。TMP 的初始化和渲染入口覆盖 UI/世界空间文本。启动时只扫描一次已有场景组件；后续不轮询场景。

数字、英文及能完整显示中文的原字体和材质保持不变。UGUI 检查原生 FontEngine 字形索引，避免 `Font.HasCharacter` 将系统回退字体误报为本字体支持中文。缺字时整段中文使用国服字体，避免一句话内中日字形混杂；Bold/Heavy/Black 字体优先选用内嵌国服 Heavy 字库。TMP 从国服材质出发，复制原有面色、描边、阴影等视觉效果。保存原字体与材质，组件复用为数字或英文时恢复。显式 `<font=…>` / `<material=…>` 标签交给 TMP 正常处理，不再吞掉。

`RichTextStyleRestorer` 保留整段翻译，根据整段包装标签、精确译词、无歧义数值恢复样式，不按字符比例猜测位置。`OfficialRichStyles` 额外加载 19,373 条完整中文样式目标及 170 个官方宏定义，恢复国服技能数值与关键词高亮，包括源文本的宏已被剥离的情况。只在目标可见文本与已选译文完全一致时使用，61 个样式冲突目标已排除，不改变翻译内容。更新数据可运行 `tools/build_rich_styles.py`，然后重新编译插件。

1.6 的 `UguiAtlasCache` 修复偶发字形碎片错位：游戏 `UICommentedText` 在 Unity `TextGenerator` 之外另存 `m_tempVerts` 顶点缓存。共享动态图集重建后，Unity 缓存已更新，游戏仍可能复用旧 UV；点击改变文本后才恢复。插件在原生 `Font.InvokeTextureRebuilt_Internal` 通知消费者之前记录图集版本，在该控件生成网格前比较图集版本与完整排版参数，变化时清除过期顶点缓存。相同条件继续复用缓存，不逐帧扫描或强制重绘整个界面，也不清除富文本内容、点击数据和原字体设置。

1.9 修复字体重建回调期间切换字体导致的列表文字空白：原生 `FontUpdateTracker.RebuildForFont` 原本遍历可变 `HashSet`，同步翻译/布局触发字体切换时会抛出 `Collection was modified` 并中断剩余控件刷新。现在先复制本轮文本快照，再通知仍有效且仍使用该字体的控件；保留 Unity 自身的跟踪集合和图集缓存失效机制。新增原生测试在 12 个普通/自定义文本的重建回调中切换字体，1.8 可复现异常，1.9 完成全部回调和网格重建。

`Resources/cn_heavy.bundle` 是国服客户端的 `SourceHanSansCN-Heavy`。`Resources/cn_song.bundle` 是国服 `方正特雅宋_GBK`，用于原宋体缺字时的同类替代，保留数字/拉丁文组件原字体。两者由 `tools/build_cn_display_font.py` 提取（宋体使用 `--role song`）；旁边的 JSON 记录字形数据和 bundle 哈希。不依赖系统安装。国服 `ui/pages/character_info_page.ab` 的 `panel_illustration_name/label_realname` 上字体加载组件 `_fontName` 为 `方正特雅宋_GBK`，`label_nickname` 为 `NotoSansHans-Medium`；日服同路径的声明一致。

`ContextTranslations` 使用独立嵌入式词库，构建命令为 `python tools/build_context_translations.py`。通过同一官方数据字段配对生成：

- `Text.textId` 匹配 main_text、init_text、string_map 的 12,619 个可译 ID，必须同时匹配该字段的源文，才优先于全局歧义词。例如设置页 `COMMON_SETTING_PCKEY_TAB` 的 Key → 按键，其他位置 Key → 钥匙仍按原词典。
- 精英化摘要和详情分别匹配实际父组件类型，按各自官方模板完整替换“标题＋天赋/技能名”，保留蓝色名称。部署费用与阻挡数模板只接受最多六位数字，保留官方颜色。未知名称或同一上下文内仍有多个译文时不匹配。
- 技能、天赋控件以及详情主姓名位置可为全局未命中项提供同字段名称补译。已有普通整句翻译继续保留。

`ComponentFields` 按原生组件的字段引用判断用途：字段必须指向当前文本，单纯位于同一页面不算匹配。1.8 的 `Resources/component_fields.json` 共 85 个类型、104 个字段，已核对美服和日服 interop。覆盖盟约、职业分支、干员正式名、基建房间/技能、公招词条、特勤任务、剧情和卫戍协议播报。只缓存反射元数据，每次赋值重新检查当前父级与字段指针。职业分支 Executor → 处决者，干员 Executor → 送葬人；干员卡片 Gnosis → 灵知，剧情称谓仍遵循原词典。原文与国服相同的正式词条也会保留，防止公招“支援”被全局词典改成“支持”。

1.7 补齐战斗顶部 `AutoChessBattleUIBondItemView`、盟约详情、商店盟约、选人详情以及战斗干员名称字段，覆盖 Precision、Solo、Elite 在局内的显示。干员名称补充官方 appellation 及大写变体；`operator-autochess` 通过双服共有活动行的 `charId` / `backupCharId` 关联国服正式名，共 705 条映射、零域内歧义。国服预备干员 Raidian 与正式干员电弧在英文中同名，活动字段使用预备干员的官方名称；Sharp、Touch、Mechanist 等国服英文代号保留。无活动专属匹配时才使用普通干员名称，仍有歧义则阻止全局误译。玩家名、玩家编号、别名及设计上独立的英文代号字段保持原文。

1.9 新增 `operator-character`，仅从正式 `char_` 行构建干员卡片、选择列表和详情名，不与地图物件/召唤物混用。Mountain → 山、Flint → 燧石、Mint → 薄绿、Vigil → 伺夜及其大写别名因此可以安全消歧。详情大名的完整已知层级也纳入优先匹配与文本稳定等待路径。原有通用、活动和玩家身份规则保留；所有旧上下文字典保持不变，干员之间仍有歧义的名称继续跳过。

1.8 补齐局内点击干员后的左侧详情：`UICharacterInfoStatusSubPanelInAutochess._basicCampText` 和 `_extraCampTexts` 分别匹配主盟约和附加盟约数组。只检查已登记的原生 `Text[]` 字段，每次读取当前元素，不缓存数组成员；空元素跳过。攻击速度 `_atkSpeed`、再部署 `_reviveTime` 与标签 `_textTags` 使用独立词库；Slow 在前者为“慢”、标签为“减速”。速度从官方 string_map 的对应 ID 配对，标签从公招 tagId 及正式干员标签列表构建，支持完整标签组合，保留源分隔符及富文本包装。召唤物详情的速度字段同时覆盖。

`ComponentTranslationStabilization` 修复 XUnity 5.6.1 的另一条遗漏路径：当控件仍处于等待文本稳定状态时，`TranslateImmediate` 不执行组件上下文回调。美服先在无绑定文本中显示 Solo，再将其复用为附加盟约字段，会稳定复现仍显示英文的问题。插件仅对已登记字段补执行原有回调，并使用 XUnity 原有的译文写入流程，保留玩家身份保护、富文本、忽略标志和写入防重入；不改动无关控件的等待流程。原生用例覆盖数组移除再加入、交换成员、空元素和面板反复启停。

原生回归额外在 14 个真实活动字段中执行每服 72 个名称、盟约、昵称和控件复用案例。连同原有每服 137 个上下文案例及 648 次提交网格检查，验证名称修复没有破坏富文本、字体图集稳定性或全局词库。

`tools/context_domains.py` 从相同稳定 ID 的两服/国服数据构建独立字段词库和有类型的动态模板。卫戍协议播报区分玩家昵称、干员名和数值：玩家名即使叫 Gnosis 也保留，干员参数 Gnosis 则译为灵知；独立昵称/玩家编号字段直接跳过翻译。特勤任务补齐未解锁状态、玩法路径和特殊干员任务表，动态玩法参数必须命中正式模式或主题名称。剧情按同文件且完全相同的指令结构对齐，补齐颜色、斜体和 `{@nickname}` 模板；不使用模糊行对齐。无法消歧的动态模板、未知玩法参数不猜译。

这些补译通过 XUnity 组件回调直接设置，不写入源文共享缓存，也不改动分发的全局 Text 词典。同字段仍有多个正式译文时阻止全局多数词条覆盖。构建旁的报告列出被排除的歧义词。

`Resources/cn_tmp_shader.bundle` 只包含标准 `TextMeshPro/Mobile/Distance Field` Shader 与 AssetBundle 元数据，共约 25 KB。它嵌入 DLL，解决客户端裁剪标准 TMP Shader 后动态字体创建失败的问题，不包含 Arial 字体或字形。来源为 XUnity 的 `TMP_Font_AssetBundles_2025-12-08.7z` 内 Unity 2021 资源；可用 `tools/build_tmp_shader.py` 从哈希锁定的缓存重新提取。

在项目根目录构建（需 .NET SDK、已初始化的客户端 interop）：

```powershell
dotnet build plugins/ArknightsLocalization.RichTextFix -c Release -p:GameInteropPath=C:/YostarGames/Arknights_JP/BepInEx/interop -p:BaseIntermediateOutputPath="$PWD/work/dotnet/ArknightsLocalization.RichTextFix/obj/"
```

输出为 `tools/runtime/ArknightsLocalization.RichTextFix.dll`。发布时需同步日服/美服运行时及启动器所选官方词库中的 DLL，并更新各自清单的 `rich_text_plugin.sha256` 及 `files` 中的 DLL 大小和哈希。原生集成检查：

```powershell
.conda-env/python.exe tests/run_font_smoke.py C:/YostarGames/Arknights_JP
.conda-env/python.exe tests/run_font_smoke.py C:/YostarGames/Arknights_EN
.conda-env/python.exe -m pytest tests/test_fonts.py -q
```

原生检查需要已安装汉化且已退出的客户端，会临时使用当前插件与新配置，验证普通 UGUI、游戏可点击富文本、TMP UI/世界文本、数字字体保留与复用恢复、Heavy/特雅宋字体、材质效果、显式数字字体标签和多图集扩容。真实技能样本包含银灰真银斩、能天使过载模式、近卫阿米娅影霄·绝影和截图中的娜仁图亚四级恶魇；逐字符验证数值蓝色。美服另检查“搜寻队友”“独立模拟”的翻译和粗体字体替代。另验证精英化父组件上下文、同一源文在复用前后的不同译文、文本 ID 优先与全局缓存隔离、字体切换发生于网格生成前、生成后字形 UV 与全新排版一致。旧版 1.3 在网格切换回归中失败，新版通过。检查后游戏自动退出，脚本恢复原插件与配置并移除检查插件。日志保存在 `work/font-smoke-*/font-smoke.log`。它验证真实 Unity 组件行为，不能替代对每个业务界面的视觉验收。

纯策略回归测试为 `python -m pytest tests/test_style_policy.py -q`，直接编译执行生产 C# 实现，覆盖语序改变、重复数值、嵌套标签、未闭合源标签、noparse、链接、官方宏与真实技能样本。`tests/real_context_cases.json` 另包含两服共 274 个官方上下文样本，包含全部职业分支、公招词条，以及本次报告的名称、盟约、基建、任务和剧情案例。原生测试在对应真实组件字段中逐项赋值，并验证同源词在旁边无关控件中仍按全局词典显示、字段指针复用后重新判断、玩家名不被替换。

`tests/FontAtlasStress.cs` 在原生客户端同时显示 36 个普通/自定义富文本控件，覆盖正文、Heavy、宋体和多字号/字重，连续 18 轮插入新字形并复用控件。截取 `CanvasRenderer.SetMesh` 实际提交的 UV，与当前字体的新排版结果比较，每服检查 648 个网格，并检查稳定后的顶点缓存仍能复用。1.5 在第 3 轮的自定义文本上复现旧 UV；当时 TextGenerator 已正确、字体跟踪正常、比较过程未触发新的图集变化，排除测试自身改变图集造成的误报。

限制：无可靠对应位置且官方样式表未覆盖的局部样式会跳过；普通 UGUI 在同一组件中只能使用一套基础字体，混合“中文说明＋数字”的组件若确实缺字，会连同其中数字一起切换。独立数字组件保留原字体，TMP 显式数字字体片段也保留。方正特雅宋、Heavy/粗体和正文分别有对应国服字库；其他特殊美术字体缺字时仍使用正文/Heavy 回退。不能从页面、文本 ID 或字段类型消除的名称歧义仍然跳过，不按出现频率猜译。
