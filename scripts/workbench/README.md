# 叙影 · 内置本地工作台

此目录是 nursing-home-montage 的运行组件，随 skill 安装。使用方式统一见 [工作台入口](../../references/workbench.md)，协调协议见 [AGENT.md](AGENT.md)。

独立使用时运行 `python3 SKILL/scripts/workbench.py --project PROJECT`，在设置页保存模型、语音和数字人连接，提交后由本机接单。macOS 后续可双击项目内的 `打开视频工作台.command`。如需在网页使用 Codex 协调，显式指定 `--host codex`。机构资料、人物、凭据、任务和成品保存在 PROJECT，安装目录只保存通用代码。

Windows 双击当前目录内的 `打开工作台.cmd`。首次启动时按提示输入项目文件夹路径，也可以在 PowerShell 运行 `& ".\打开工作台.cmd" --project "D:\养老视频项目"`。需要 Python 3.11 或更高版本。

制作完成为待验收。列表和详情可预览清理范围并验收；验收后保留所有成品版本和最少任务记录，清理中间文件。封面更新或内容重做仍在同一 FIFO。
