# Photopea 图片处理 Skill

通过 Codex 执行工具、Playwright、Photopea 官方 Live Messaging API，以及 Pillow／NumPy，处理图片裁剪、批量拆分、清边、方图、预览和打包。

不依赖 MCP，也不自动化 Photoshop 等桌面图像软件界面。

## 安装与调用

把仓库内容放进 Codex 的技能目录，例如：

- 个人安装：`$CODEX_HOME/skills/photopea-image-edit/`
- 项目安装：`.agents/skills/photopea-image-edit/`

之后在 Codex 中使用 `$photopea-image-edit` 调用。运行时需要 Node.js、Playwright，以及含 Pillow 和 NumPy 的 Python 环境；技能会按当前环境发现运行时，不绑定特定机器路径。

## 文件结构

- `SKILL.md`：适用范围、执行流程和安全约定
- `scripts/photopea.cjs`：Playwright 浏览器会话与 Photopea 官方脚本 API 桥接
- `scripts/gutters.py`：按引导线细化不规则分割边界
- `scripts/postprocess.py`：局部清边、PNG 方图、预览图、校验和 ZIP 打包
- `references/workflows.md`：工作流、计划文件格式和调用示例
- `agents/openai.yaml`：Codex 技能显示信息

## 官方资料

- [Photopea API](https://www.photopea.com/api/)
- [Photopea Live Messaging](https://www.photopea.com/api/live)
- [Photopea 脚本](https://www.photopea.com/learn/scripts)
