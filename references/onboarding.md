# 初始化与人物库

实际制作默认通过[内置工作台](workbench.md)配置，用户在页面填凭据和人物。以下 CLI 只用于无页面模式、补缺或迁移。

所有命令由智能体运行，用户通过对话提供资料和偏好，或在本地页面输入凭据。用工具workdir进入用户项目；技能可以位于任意路径，以下SCRIPT表示技能scripts目录。可为project.py/batch.py显式传全局参数 --project <项目根>；从已有任务恢复会读取runtime绑定项目。

## 一次配置

1. `python SCRIPT/project.py init` 幂等创建 `.montage/project.json`，不覆盖已有配置。
2. `python SCRIPT/project.py doctor` 只返回缺项布尔值、人物状态和工具可用性，不回显密钥。
3. `python SCRIPT/project.py setup-ui` 返回仅绑定127.0.0.1的临时设置页链接。让用户在本地页面填写一次：语音API Key、视频AK/SK。文件中转默认 tmpfile，无需账号、Key 或存储桶；仅选择 TOS 时补存储桶/区域/Endpoint。留空保留现有值。页面不访问外部脚本、不记录输入日志，30分钟无操作后停止。
4. 若用户已提供凭据文件，智能体读取其格式并转换为临时私有JSON，执行 `project.py credentials --file <文件> --connection main`。字段为api_key/ak/sk，选配storage_provider（tmpfile/tos）及bucket/region/endpoint；导入后清理临时副本，输出只报告成功和缺项。已有环境变量或macOS钥匙串可通过连接引用绑定；配置中保存引用，不把Key写入公开配置。Windows/Linux使用环境引用或项目私有文件。
5. 通过 `project.py configure --file <智能体生成的补丁JSON>` 保存settings、connections、default_person等用户偏好。未指定项保留原值。

当前原生适配器为火山语音V3 + OmniHuman2.0，默认通过 tmpfile 中转驱动音频，TOS 选配。服务凭据每项目连接配置一次，几十个人物共享同一连接。不要假设任意一个Key能跨服务通用。当前用户的旧项目可以把已存在的凭据路径和钥匙串引用绑定到连接，不要求重新填写。

官方入口：[语音控制台](https://console.volcengine.com/speech/app)、[访问密钥](https://console.volcengine.com/iam/keymanage/)、[TOS](https://console.volcengine.com/tos)。根据接口实际权限提示定位服务开通/音色资源；不伪造权限检查通过，不为检查凭据偷偷生成几十段视频。

## 数字人上传与批量导入

工作台「数字人 → 添加数字人」分别选择一张人物图片和一段录音，两项必填。保存时，原始文件和制作所需的 PNG、WAV 一同保存到当前项目 `.montage/people/<人物ID>/`。

推荐但不强制：一个人物文件夹放一张正面图和一段干净单人录音，文件名任意。也支持同目录的同名图片+音频配对。

`uv run --python 3.11 SCRIPT/batch.py --help` 使用自动依赖；人物导入和训练命令用 `uv run --python 3.11 SCRIPT/project.py import-people <目录> --connection main`（project.py依赖均为标准库，媒体探测依赖ffmpeg/ffprobe）。

导入按名称和素材指纹去重，复制到.montage/people；图片规范为PNG，声音规范为单声道WAV，不改变语速。查看 `project.py people`。人物可先只有图片，随后绑定已有音色；缺音频且没有音色时保持needs_voice，不算就绪。

先在「设置 → 语音合成与复刻」选择默认语音平台，页面只显示这个平台的语音配置。新人物沿用默认，也能在「数字人」单独选择语音平台。已有音色时填所选平台的音色 ID。没有音色时可留空，保存人物和录音后点击「复刻」。火山直接读取项目的预付费槽位，优先使用空位。选择已有音色时需确认覆盖；用于其他人物的槽位会在复刻成功后改绑。MiniMax 使用自己的 API Key，复刻后自动绑定 Voice ID，正式配音会使用该人物的 MiniMax 音色。数字人视频由火山生成，使用单独配置的火山 AK/SK。

- 已有音色：`project.py bind <人物名或ID> --speaker <音色ID>`；自定义音色另加 --custom <ID>，speaker为custom_speaker_id。
- 默认人物：`project.py default <人物名或ID>`。
- 只有录音：`project.py train <人物名或ID>`。先在连接里选clone_mode：prepaid使用speaker_pool空闲槽位；postpaid创建独立自定义音色，首次正式合成会产生平台槽位费。服务模式未选择时只补问这一项。也可单次 --speaker 指定未占用槽位。
- 训练只执行一次并持久保存记录，后续相同命令查询。状态不明时不重训；状态成功后自动绑定，不重新训练已配置音色。不同人物不复用/覆盖同一预付费音色槽位。

声音样本不是未来每条视频的驱动音频。每条仍按新文案生成该人物的完整旁白，再只取首尾制作数字人。

## 用户配置示意（智能体维护）

connections.main包含speech_key/access_key/secret_key引用、storage、clone_mode、speaker_pool。people.<id>包含name、portrait、sample、speaker、custom_speaker_id、connection、speech_rate、status。所有相对资产路径均相对于.montage；用户在对话提供的外部路径应转换为绝对路径或复制到.montage/assets，避免依赖技能目录。

storage 默认 `{"provider":"tmpfile"}`；无需填写其他字段。选择 TOS 使用 `{"provider":"tos","bucket":"用户存储桶","region":"cn-beijing","endpoint":"tos-cn-beijing.volces.com"}`。旧连接已有 bucket 但未写 provider 时继续按 TOS 处理，避免升级改变现有任务；明确切换时只改 storage.provider，保留原桶配置供以后选用。doctor 的 storage=true 仅代表配置齐全，不代表联网或数字人抓取已验证。

正文字幕默认包内霞鹜文楷，标题和标签默认包内 Noto；项目可用 settings.typography.body_font 和 collection_index 指定已确认字体。默认配乐和替换／关闭方式见[运行](runtime.md#默认配乐)，不依赖开发者电脑的资源路径。

## 身份与可选咨询钩子

每个人物绑定一个 social_media_name；用户给一个名字即可，不要求平台、ID 或主页。缺失时补问，不能从旧文案猜测。人像＋社媒名是身份输入，语音提供录音或已有音色 ID；spoken_name 沿用已明确的口播称呼。封面优先使用 cover_portrait 私有透明 PNG，否则从人物原图本地抠像并缓存。系统生成 ID、规范图片、训练／任务记录和哈希，用户不用填写。

新增人物时，可自填钩子、从 [咨询钩子](closing-hooks.md) 推荐的 3–5 条中选定或编辑，也可跳过。只存已选条目，不存全部推荐草稿。导入允许未完成身份；生产前按本次功能补齐署名／音色，个人钩子库为空不阻塞。服务凭据仍按项目连接共享。

智能体将用户已指定内容保存为补丁，运行 `project.py identity <人物名或 ID> --file <补丁>`；可用字段 social_media_name、spoken_name、cover_portrait、closing_hooks。closing_hooks 为 `{"default_id":null,"library":{"consult":{"text":"用户已选择的咨询原文","keyword":"已确认关键词"}}}`，default_id 仅在用户指定默认条目时保存。

当前项目的固定头标保存 settings.cover.header_text，首次确认后每条继承。人物姓名、社媒名、人像、音色和个人钩子都留在 .montage，不写入通用 preset。没有背景音乐时明确输出无 BGM，不借用开发者私有曲目。
