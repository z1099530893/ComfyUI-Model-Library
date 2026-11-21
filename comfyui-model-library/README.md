# ComfyUI 本地模型库（Model Library）

在 ComfyUI 左侧工具栏增加「本地模型库」侧边栏，用来浏览本机模型、查看示例图和说明，并把模型拖到画布生成对应的加载节点。

当前版本：v0.1.3

插件只读取 ComfyUI 自己配置的模型目录，**不会移动、改名或删除任何模型文件**。

---

## 安装

### 方式一：Release 包

1. 从 [Releases](https://github.com/z1099530893/ComfyUI-Model-Library/releases/tag/plugin-v0.1.3) 下载 `ComfyUI-ModelLibrary-v0.1.3.zip`；
2. 解压到 `ComfyUI/custom_nodes/`，确保得到：
   `ComfyUI/custom_nodes/ComfyUI-ModelLibrary/__init__.py`
3. 重启 ComfyUI，刷新浏览器页面（`Ctrl+F5`）；
4. 点击左侧工具栏底部的数据库图标，打开「本地模型库」。

### 方式二：手动复制

把本目录复制到 `ComfyUI/custom_nodes/ComfyUI-ModelLibrary`，然后重启 ComfyUI。

开发时可以用目录联接，修改代码后刷新页面即可：

```bat
mklink /J "G:\ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-ModelLibrary" "F:\Lora_Downloads\comfyui-model-library"
```

---

## 功能

- 扫描 ComfyUI 的模型目录：Checkpoint、UNET（diffusion_models）、LoRA、VAE、CLIP、Upscale、ControlNet、CLIP Vision、Embedding。
- 类型筛选固定为：全部、Checkpoint 模型、UNET 模型、LoRA 模型、VAE 模型、CLIP 模型、Upscale 模型；ControlNet、CLIP Vision、Embedding 只出现在「全部」里。
- 搜索、类型筛选、基础模型筛选、排序、网格/列表视图。
- 详情页：示例图轮播、触发词、作者、大小、时间、说明文档、来源网页。
- 将卡片拖到画布即可插入加载节点（落在鼠标位置并选中，不自动接线）：

| 模型类型 | 节点 | 参数 |
| --- | --- | --- |
| Checkpoint 模型 | CheckpointLoaderSimple | ckpt_name |
| LoRA 模型 | LoraLoaderModelOnly | lora_name |
| UNET 模型 | UNETLoader | unet_name |
| VAE 模型 | VAELoader | vae_name |
| CLIP 模型 | CLIPLoader | clip_name |
| Upscale 模型 | UpscaleModelLoader | model_name |

- ControlNet、CLIP Vision、Embedding 只浏览、复制名称、打开文件夹，不创建节点。
- LoRA 默认插入「LoRA加载器（仅模型）」（`LoraLoaderModelOnly`），只输出 MODEL；需要 CLIP 输出时可在画布上手动换成「加载LoRA」。

<img src="../docs/images/plugin-library.png" alt="侧边栏模型库：类型筛选、基础模型筛选与卡片列表" width="360">

<img src="../docs/images/plugin-detail.png" alt="插件详情页：示例图轮播、插入节点、打开文件夹与说明文档" width="360">

- NSFW 封面默认模糊，面板右上角统一开关；详情页内可单独显示/重新模糊。
- 详情页「打开文件夹」会在资源管理器中定位模型（Windows）。
- 缩略图缓存在 `cache/thumbs`，可随时删除，会自动重建。

---

## 说明与限制

- 需要带侧边栏扩展 API 的较新 ComfyUI；开发验证版本为 ComfyUI 0.45.0、前端 1.59.1。
- 不依赖额外的 Python 包，使用 ComfyUI 自带的 Pillow。
- 只读：不修改模型文件；删除、改名请使用下载器或资源管理器。
- 侧边栏名称使用「本地模型库」，避免和 ComfyUI 自带的「模型库」重名。

---

## 相关项目

模型下载器与插件在同一个仓库：

https://github.com/z1099530893/ComfyUI-Model-Library
