---
name: photopea-image-edit
description: 通过 Playwright 和 Photopea 官方脚本 API 编辑已有图片，配合 Pillow/NumPy 批量裁剪表情或素材、清理相邻残边、生成方图与预览并打包。适用于精确裁剪、缩放、旋转、图层或 PSD 脚本处理；采用直接执行脚本的方式，不使用 MCP 或桌面软件界面自动化。生成新画面、重绘人物和风格转换使用生图工具。
---

# Photopea 图片处理

调用链：**Codex 执行工具 → Node.js／Playwright → Photopea Live Messaging API → Python／Pillow／NumPy → 本地成品**。

直接调用随附脚本，不安装或注册 MCP 服务。浏览器在独立的无头会话里运行在线 Photopea；宿主页通过 Playwright 路由注入，不启动本地 HTTP 服务。文件用 `ArrayBuffer` 传入，编辑由官方脚本接口执行。

## 先确定图与操作

1. 用 `view_image` 查看原图，再用 `scripts/postprocess.py inspect --input <图片>` 读取尺寸、方向、透明通道和帧数。多帧图片、PSD 的图层或动画需求要单独确认处理范围，随附打包脚本只交付静态 PNG。
2. 对拼图，数清真实素材，去掉标题区域，保留文字、装饰、角和尾巴。根据每行布局指定边界；不等宽排布不要直接等分网格。裁剪只能保留已有像素，重叠区域不能靠裁剪恢复。
3. 在当前任务的可写目录保存源图副本（需要归一化方向时）、计划 JSON、原始导出与交付成品。保留原上传文件，避免覆盖。
4. 通过可用的 `load_workspace_dependencies` 查询 Node、Python 与包位置；也可使用已安装运行时。Node 需要 `playwright` 或 `playwright-core`，Python 需要 Pillow 和 NumPy。不把某个账号、日期、附件路径或运行时版本写进计划。

## 执行图片操作

计划格式、批量裁剪例子及单图脚本编辑见 [references/workflows.md](references/workflows.md)。

- 普通裁剪：直接提供每个 job 的 `box: [left, top, right, bottom]`，坐标均为原图像素，右、下边界不包含。
- 不规则表情拼图：先给出行范围、每个表情名称及空隙的大致走向，运行 `scripts/gutters.py` 生成沿浅色空隙细化的裁剪计划和分界预览。它需要人工/视觉定位的引导线，不自动识别人物；深色、纹理背景或明显重叠时改用直接指定多边形。
- 旋转、缩放、图层等：在 job 的 `script` 内写短 Photopea 脚本；如果操作改变尺寸，给出 `expected_size`。随附执行器会在裁剪之后执行 `plan.script`，再执行 `job.script`，最后导出。
- 每个 job 都从相同原文件重新载入，互相独立。需连续编辑同一张图时，把相关操作放到一个 job 内。

通过执行工具运行以下命令。路径替换为本机实际值；在 PowerShell 中，对带空格或单引号的路径正确引用，脚本正文放 JSON 文件中，不拼入 shell 命令：

```text
<node> <skill>/scripts/photopea.cjs --input <源图> --plan <plan.json> --out <raw目录> --playwright-module <playwright包目录>
```

默认优先使用 Playwright 配套 Chromium；Windows 配套版本缺失时尝试已安装 Edge、Chrome。也可用 `--channel msedge` 或 `--browser-executable <绝对路径>` 指定。不要为处理一张图重复下载浏览器、改用户浏览器配置或关闭浏览器安全限制。

执行器支持 PNG、JPG、WebP、PSD 导出。需要后续清边和打包时选 PNG；其他格式单独核对成品。它会检查输出签名、可验证的预期尺寸，记录文件哈希，并在失败时留下诊断截图和已完成任务记录。

## 清边、方图与打包

```text
<python> <skill>/scripts/postprocess.py package --source <源图> --plan <plan.json> --raw <raw目录> --out <交付目录>
```

默认生成原尺寸 PNG（边缘加 8 像素留白）、512×512 等比例方图、带名称的总览图、检查记录及 ZIP。`--square 0` 可省去方图；`--padding`、`--inset`、`--font` 可以调整。

- 裁剪计划的 `keep_polygon` 保留目标区域，`erase_polygons` 清除个别相邻残边；两者使用原图坐标，只用于未再缩放旋转的纯裁剪 job。
- 根据原图底色明确设置 `background`，避免把白色人物或浅色头发当背景抠除。原图透明通道默认保留；只有用户需要透明底时才安排完整抠图。多边形外透明留白不等于人物背景已抠除。
- 打包前校验源文件、原始导出哈希、数量、尺寸与文件可读性。纯裁剪的常见位图还会比较原图对应区域的可见像素；有编辑脚本的输出需要按操作检查画面和预期尺寸。
- 用 `view_image` 看分界预览、成品总览和拥挤边缘的单图。发现带入邻图、切断文字或不合理留白时，调整边界/遮罩再导出或后处理。遮罩本身调整不必重跑 API。
- 交付 ZIP 与预览的绝对路径链接，说明数量、尺寸版本和实际背景状态。方图的放大不增加原图细节。

## 本次实践留下的接口注意点

- Photopea 的脚本解释器并非完整 Photoshop 或浏览器 JavaScript；先验证实际输出，不凭方法名认定支持。`Document.duplicate()` 的返回值和行为在本次任务中不可靠，批量裁剪使用重新载入原文件。
- 脚本调用必须串行。执行器给每次脚本附加唯一结束标记，并等待标记之后的完成信号及二进制输出，避免前一次的 `done` 污染下一次。
- API 的完成回包不等于操作有效；核对导出像素、尺寸或图层效果。脚本报错可能中断解释器；保留诊断，修正原因后用新的浏览器会话重跑一次。相同失败再现时换处理路径或报告阻塞，不循环重试。
- 遇到网络拒绝、浏览器缺失等通用问题，先查官方/项目资料，再按本地证据修复。需要联网权限时按当前执行环境处理，不把一次性提权写成所有图片任务的固定要求。
- 保留 Photopea 站点正常行为，不触发登录、订阅或付费生成操作。此技能只围绕文件与脚本接口工作。

## 来源

- [Photopea Live Messaging](https://www.photopea.com/api/live)：文件与脚本传入、二进制结果传出。
- [Photopea Scripts](https://www.photopea.com/learn/scripts)：脚本模型与导出格式。
- [sportiz91/photopea-api](https://github.com/sportiz91/photopea-api) 及其 [RECON.md](https://github.com/sportiz91/photopea-api/blob/main/RECON.md)：参考独立浏览器会话、结束标记和实际输出验证的思路。本技能的脚本为本次已验证流程的独立实现，没有引入该项目或其服务。
