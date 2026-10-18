# 计划格式与运行方法

## 运行时与路径

使用执行工具调用 Node 与 Python。若运行时的包目录不在默认搜索路径，给 `photopea.cjs` 的 `--playwright-module` 传入 `.../node_modules/playwright` 或 `.../node_modules/playwright-core` 的绝对目录；不要假设 skill 文件夹能直接 `require` 到工作区包。

PowerShell 示例，变量值需要按本机依赖查询结果替换：

```powershell
& $nodeExe $photopeaScript --input $sourceFile --plan $planFile --out $rawDir --playwright-module $playwrightPackage --channel msedge
& $pythonExe $postprocessScript package --source $sourceFile --plan $planFile --raw $rawDir --out $deliveryDir
```

计划文件使用 UTF-8（支持 BOM）。`--out` 使用新的目录；确实要更新同一次处理的成品时才加 `--overwrite`。打包脚本会拒绝夹杂旧批次图片的目录。

## 直接裁剪计划

以下只是坐标格式示例，先查看实际图片并另定边界：

```json
{
  "source_size": [800, 400],
  "format": "png",
  "background": [255, 255, 255, 255],
  "jobs": [
    {
      "name": "01_开心",
      "box": [0, 0, 400, 400],
      "expected_size": [400, 400]
    },
    {
      "name": "02_抱抱",
      "box": [400, 0, 800, 400],
      "keep_polygon": [[400, 0], [799, 0], [799, 399], [400, 399]],
      "erase_polygons": [[[400, 0], [410, 0], [400, 12]]]
    }
  ]
}
```

`source_size` 可选，用于核对 Photopea 读入的画布。所有 `box` 坐标为整数，位于原图范围内。每个名称必须唯一且是合法文件基名，不含目录、扩展名或 Windows 保留字符。

`background` 可设 RGB、RGBA 或 `"transparent"`。未指定时，有透明像素的原始导出采用透明补边，否则采用白色；浅粉、浅紫等原底色应在查看原图后明确给出。不要使用透明补边冒充完整抠图。

`expected_size` 对 PNG、PSD 可在 API 脚本中直接验证。JPG／WebP 可以直接导出，不填写这个字段时由代理用本地解码工具核对；PNG 打包脚本不接收这些格式。

## 沿浅色空隙细化边界

当表情排布不等宽、文字伸入邻格时，可先手工/视觉确定空隙引导线，再用 NumPy 寻找附近较少穿过深色轮廓的路径。输入布局例子：

```json
{
  "background": [253, 250, 253, 255],
  "rows": [
    {
      "top": 0,
      "bottom": 240,
      "left": [[0, 315], [80, 310], [239, 310]],
      "right": 1200,
      "radius": 12,
      "names": ["01_开心", "02_抱抱", "03_好耶"],
      "guides": [
        [[0, 600], [100, 605], [180, 625], [239, 630]],
        [[0, 850], [100, 860], [180, 900], [239, 905]]
      ]
    }
  ]
}
```

引导点使用 **[y, x]**；多边形使用 **[x, y]**。两者特意不同：引导线按图像行插值，多边形遵循绘图坐标。

```text
<python> <skill>/scripts/gutters.py --input <源图> --layout <layout.json> --out <plan.json> --preview <分界预览.png>
```

每行的名称数等于内部引导线数加一。`left`、`right` 可为固定横坐标，也可为 [y,x] 引导点列表；默认是画布左右边缘，用于避开标题等非表情区域。`radius` 表示搜索带宽，不是抠图精度。这个算法依赖浅色、相对空白的间隔；看预览确认分界未切进角色，尤其是头发、尾巴和文字。少量交叠可追加 `erase_polygons`；明显遮挡不能凭空恢复。

## 单图旋转、缩放及自定义编辑

```json
{
  "jobs": [
    {
      "name": "01_rotate",
      "script": "app.activeDocument.rotateCanvas(90);",
      "expected_size": [300, 500]
    },
    {
      "name": "02_thumbnail",
      "script": "app.activeDocument.resizeImage(250,150,72,ResampleMethod.BICUBIC);",
      "expected_size": [250, 150]
    }
  ]
}
```

上例假定原图 500×300；每个 job 都从原文件独立开始。`plan.script` 适合所有 job 都需要执行的公共脚本。执行顺序为 **重新载入 → 设置像素单位 → box 裁剪（如果有）→ 公共脚本 → job 脚本 → 导出 → 校验**。

自定义脚本只写编辑动作，不要自行调用 `saveToOE`、关闭文档、改变宿主页通信或打开远程文件。执行器负责唯一一次导出与完成回包。需要字体/其他文件时，应先扩展会话进行显式二进制载入，并用小图实测；不把未验证的 Photoshop 方法当作既有能力。

`PhotopeaSession` 和 `exportPlan` 也可以从 `photopea.cjs` 导入，供当前任务的临时 Node 脚本复用。保留 `try/finally` 关闭独立浏览器；操作状态不依赖用户已打开的浏览器窗口。

## 后处理与检查

```text
<python> <skill>/scripts/postprocess.py inspect --input <源图>
<python> <skill>/scripts/postprocess.py package --source <源图> --plan <plan.json> --raw <原始导出目录> --out <交付目录> --square 512 --padding 8 --inset 16
```

每张原尺寸图保留原像素内容，增加指定留白；方图等比例放入画布，不拉伸。方图放大不会生成额外细节。`--square 0` 仅输出原尺寸版本，`--font` 可指定支持中文的字体。大量图片每 50 张分为一张预览，避免遗漏。

`photopea-manifest.json` 记录原始 API 导出；`verification.json` 记录后处理检查。裁剪框或编辑脚本改变后需重跑 API；只改名称标签、背景或多边形清边参数时，可重新运行后处理。文件基名改变仍需重新导出，否则与 manifest 不匹配。

对于纯裁剪的常见位图，默认检查经 EXIF 方向归一化后的原图区域与 API 导出的可见像素，允许 JPEG 解码的微小差异。PSD、有色彩转换或自定义脚本时用尺寸、图层信息及图像实际效果验证；`source_checked: false` 不能写成“像素完全一致”。`verify_source: false` 只用于确认不适用的情况，说明原因，勿用它掩盖导错图。

网络失败时留下的 `photopea-diagnostic.png/json` 与 `photopea-partial.json` 用于定位。先修复具体原因，重新启动会话；不要在可能已经损坏的脚本解释器里连续补发动作。已经写出的原始结果不会被自动删除。
