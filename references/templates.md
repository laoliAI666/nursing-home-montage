# 正文视频样式库

默认正文样式为 01 号认可基线 `fullscreen-orange`（1080×1920、30fps、实拍全屏、手撕橙条机构牌、白／粉手写字幕与黄色重点词），保留其完整锁定渲染路径以确保零漂移。同时内置 30 套与封面样式配套的视频样式（`assets/styles/catalog.json`），统一使用包内 `NotoSansSC-Black.otf`，支持左/右出镜人物、多风格顶栏/章节牌与描边/胶囊高亮/半透底条字幕，并支持通过 `.montage/styles/videos/` 放入 JSON 扩展更多样式。每位数字人可在工作台「数字人」编辑页或「风格库」页面绑定专属封面与视频样式，优先级为 `plan.settings > 数字人绑定 > project.json > preset.json`。默认值来自 preset.json 与 assets/templates/fullscreen-orange.json，不在文档复制参数表。

## 字体与字形

字幕默认包内霞鹜文楷，标题／章节默认包内 Noto。已确认项目字体由 settings.typography.body_font、collection_index 覆盖；标题／标签可使用 template.overrides.fonts。缺字体或缺字明确停止，不静默换成最粗黑体。任务冻结字体副本，不依赖更新前的安装路径。

输出1080×1920，沿已确认参考坐标绘制并按输出像素测量；保留已认可的字号、描边、倾斜、行距与阴影。短章节名保留字号，长标签只扩展底板，不压缩字形。字幕按固定字号测量真实字形、描边、阴影和重点词，不拉扁字形、不为塞字缩小全片字幕。封面字号自适应与正文字幕互不混用。

## 字幕和位置

现有语义断行优先；超宽自动重排，超过两行时用已核验时间拆cue。数字及单位整体保留；缺可靠时间或会切入数字时间组时停止，请调整分组。保留全部原词、音频时长和镜头时长，不通过变速贴旧时长。

人物只读实际 avatars.json，不凭模板增加结尾人物。字幕与人物按[项目测量的UI](platform-ui.md)避让，不能把某台设备尺寸称为平台统一标准。所有字体最终检查复杂字、短章节、长院名、数字单位及高亮放大词。

## 重新排版

`montage.py revise SOURCE --out NEW` 另存，复用匹配的声音与口型。旧任务仍按原冻结引擎恢复；新版包删除其他正文渲染器，不能在旧工程里直接更换 runtime。详见[另存](revisions.md)。

check若仅有对比度错误，先看实际描边与背景。仅在所有非contrast错误已修复、实际帧可读时，用 review-contrast --frames ... --note ...记录限定复核；原始失败与 fullCheckPassed=false保留，不可称为全部自动检查通过。
