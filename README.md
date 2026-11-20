# ComfyUI 本地模型工具集

这个仓库包含两个配合使用的工具，目标是打通「下载模型 → 整理归档 → 在 ComfyUI 里浏览 → 拖进工作流」的完整流程：

| 工具 | 作用 | 当前状态 |
| --- | --- | --- |
| **模型下载器**（model-downloader） | 从模型站点下载模型，自动归档、写说明、生成示例图索引，并提供本地模型库管理界面 | v0.4.0 开发版，仅支持 Civitai，暂无 exe |
| **本地模型库插件**（comfyui-model-library） | 在 ComfyUI 侧边栏浏览本机模型，查看示例图和说明，一键/拖拽插入加载节点 | v0.1.0，功能可用 |

两个工具共享同一套目录和元数据约定（`model.json`、`模型使用说明.txt`、示例图），可以各自独立使用，也可以搭配使用。

---

## 目录结构

推荐把仓库克隆为一个 `Lora_Downloads` 文件夹，保持和开发时一致的布局：

```
Lora_Downloads/
├─ README.md
├─ docs/
│  ├─ interaction-rules.md      # 人机协作规则
│  ├─ backup.md                 # 代码备份逻辑与备份节点
│  └─ roadmap.md                # 开发计划与当前阶段
├─ model-downloader/            # 模型下载器（Python + 本地网页界面）
│  ├─ app.py
│  ├─ core/
│  │  ├─ config.py
│  │  ├─ downloader.py
│  │  ├─ library.py
│  │  ├─ organizer.py
│  │  ├─ scanner.py
│  │  └─ sites/
│  ├─ web/
│  ├─ assets/
│  ├─ tools/
│  ├─ requirements.txt
│  └─ 启动模型下载器.bat
└─ comfyui-model-library/       # ComfyUI 插件（后端 + 前端）
   ├─ __init__.py
   ├─ backend/
   │  ├─ routes.py
   │  ├─ scanner.py
   │  └─ thumbs.py
   └─ web/
```

说明：

- 下载器目前**没有 exe**，需要通过 Python 运行。后续提供 exe 时，仍然放在 `model-downloader/` 下（例如 `model-downloader/release/`），目录结构不变。
- 插件不需要安装第三方 Python 包，复用 ComfyUI 自带的 Pillow。
- 插件运行后会在自己目录下生成 `cache/thumbs`（缩略图缓存），可随时删除，会自动重建。

---

## 环境要求

- Windows 10 / 11（下载器依赖 Windows 的资源管理器、回收站等能力）
- Python 3.11（下载器；开发与测试使用 3.11）
- 已安装 ComfyUI（插件需要）
- 下载器需要能访问 Civitai；如网络受限，可在设置里填写代理

---

## 快速开始

### 1. 获取代码

```bat
git clone https://github.com/z1099530893/ComfyUI-Model-Library.git Lora_Downloads
```

也可以直接下载 ZIP 解压，保证 `model-downloader` 与 `comfyui-model-library` 在同一级目录即可。

### 2. 安装并运行模型下载器

```bat
cd Lora_Downloads\model-downloader
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装完成后，双击 `启动模型下载器.bat` 即可（脚本会用 `.venv` 里的 `pythonw.exe` 启动，不弹黑框）。

首次使用建议按顺序配置：

1. **保存到**：默认下载目录，例如 `G:\ComfyUI_windows_portable\ComfyUI\models`。
2. **设置 → 模型库扫描范围**：把同一个 `models` 目录加进去，下载器就能显示已有模型。
3. **设置 → 站点 Token**：Civitai 的公开模型通常不需要；需要登录/gated 内容时再填。
4. **设置 → 代理**：直连失败时填写，例如 `http://127.0.0.1:7890`。

下载器的配置和任务记录保存在：

```
%LOCALAPPDATA%\ModelDownloader\config.json
%LOCALAPPDATA%\ModelDownloader\tasks.json
%LOCALAPPDATA%\ModelDownloader\hashes.json
```

卸载或迁移时，删除/备份这个目录即可，不会影响模型文件。

### 3. 安装 ComfyUI 插件

把 `comfyui-model-library` 放到 ComfyUI 的 `custom_nodes` 下，目录名建议为 `ComfyUI-ModelLibrary`。

方式一：Release 包（推荐，普通用户）

1. 打开 [Releases](https://github.com/z1099530893/ComfyUI-Model-Library/releases/latest)；
2. 下载 `ComfyUI-ModelLibrary-v0.1.0.zip`；
3. 解压到 ComfyUI 的 `custom_nodes` 目录，确保路径为：

```
ComfyUI/custom_nodes/ComfyUI-ModelLibrary/__init__.py
```

方式二：复制源码（简单，但改代码后需要重新复制）

```bat
xcopy /E /I "F:\Lora_Downloads\comfyui-model-library" "G:\ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-ModelLibrary"
```

方式三：目录联接（开发时推荐，改动源码即时生效）

```bat
mklink /J "G:\ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-ModelLibrary" "F:\Lora_Downloads\comfyui-model-library"
```

然后：

1. 重启 ComfyUI；
2. 刷新浏览器页面（建议 `Ctrl+F5`）；
3. 点击左侧工具栏底部的数据库图标，打开「本地模型库」。

插件读取的是 ComfyUI 自己配置的模型目录（`folder_paths`），不需要在插件里再填路径。新增模型后点面板右上角的刷新按钮即可。

---

## 功能说明

### 模型下载器

**下载与归档**

- 解析 Civitai 模型页/版本页链接，读取标题、版本、作者、基础模型、触发词、标签、NSFW 等元数据。
- 断点续传：先写 `.part` 文件，再用 HTTP Range 续传；支持暂停、继续、取消。
- 按模型类型自动归档到 `checkpoints`、`diffusion_models`、`loras`、`vae`、`text_encoders`、`upscale_models`。
- 模型文件平铺在类型目录下，说明和示例图放在同名文件夹里，ComfyUI 下拉列表不会多出一层目录。
- 自动下载示例图、生成 `模型使用说明.txt`、写入 `model.json`、记录 SHA256。

**本地模型库**

- 扫描「设置 → 模型库扫描范围」中的目录，读取 `model.json` 和说明文档；老模型没有元数据时会回退到目录名/说明文档推断。
- 搜索、类型筛选、基础模型筛选、排序、网格/列表视图、卡片大小调节。
- 模型详情：示例图轮播、触发词、作者、大小、时间、来源网页、说明文档。
- NSFW 封面默认模糊，可在右上角统一开关；详情内可单独显示/重新模糊。
- 批量选择、批量「补数据」（按 SHA256 到 Civitai 匹配并补全元数据）、批量移入回收站（模型文件 + 同名资料夹一起删除，可从回收站恢复）。
- 右键菜单：打开文件夹、复制信息、移入回收站。
- 设置：默认目录、最近目录、模型库目录、ComfyUI 目录、Token、代理、缓存清理。

**当前限制**

- 站点适配器目前只有 Civitai；界面上的 HuggingFace / ModelScope 入口属于后续计划。
- 暂无 exe，需要 Python 环境运行。
- 开发阶段暂停新功能，优先完善插件与文档。

### ComfyUI 本地模型库插件

- 在 ComfyUI 左侧工具栏新增「本地模型库」标签页，不占用画布空间。
- 只读扫描 ComfyUI 的模型目录，覆盖：基础模型、LoRA、扩散模型、VAE、文本编码器、放大模型、ControlNet、CLIP Vision、Embedding。
- 卡片展示封面、名称、类型、基础模型；支持搜索、类型筛选、基础模型筛选、排序、网格/列表视图。
- 详情页：示例图轮播、触发词、作者、大小、时间、说明文档、来源网页。
- 直接拖拽卡片到画布即可插入加载节点，落在鼠标位置并自动选中，不自动接线：

| 模型类型 | 生成的节点 | 参数 |
| --- | --- | --- |
| 基础模型 Checkpoint | CheckpointLoaderSimple | ckpt_name |
| LoRA | LoraLoader | lora_name |
| 扩散模型 UNET | UNETLoader | unet_name |
| VAE | VAELoader | vae_name |
| 文本编码器 CLIP | CLIPLoader | clip_name |
| 放大模型 UpscaleModel | UpscaleModelLoader | model_name |

  其余类型（ControlNet、CLIP Vision、Embedding）只浏览、复制名称、打开文件夹，不创建节点。

- 卡片 `+` 按钮和详情页「插入节点」按钮效果相同，适合不方便拖拽时使用。
- NSFW 封面默认模糊：面板右上角按钮统一控制全部缩略图；详情页内点图片或右上角眼睛按钮可单独显示/重新模糊。
- 详情页「打开文件夹」会在资源管理器中定位模型文件。
- 纯只读：不移动、不改名、不删除模型文件。

---

## 数据约定

下载器写下的元数据，插件可以直接读取：

```
models/loras/<模型文件名>.safetensors
models/loras/<模型文件名>/
    ├─ model.json                 # 程序读取的元数据
    ├─ 模型使用说明.txt            # 给人看的说明
    ├─ 示例图_01.jpg
    └─ 示例图_02.jpg
```

`model.json` 主要字段：

```json
{
  "schema": 1,
  "site": "civitai",
  "model_id": 123456,
  "page_url": "https://civitai.com/models/...",
  "title": "模型标题",
  "version_name": "v1.0",
  "model_type": "lora",
  "base_model": "Illustrious",
  "trigger_words": ["xxx"],
  "nsfw": false,
  "file": { "name": "xxx.safetensors", "size": 123, "sha256": "..." },
  "images": ["示例图_01.jpg"],
  "downloaded_at": "2026-11-19T20:00:00+08:00"
}
```

没有 `model.json` 的旧模型也能显示：插件会读取 `模型使用说明.txt`，并从类型目录推断基础模型。

---

## 常见问题

**Q：下载器和插件的模型数量不一致？**

插件只统计 ComfyUI 可加载的模型目录；下载器扫描整个 `models` 目录，会把 `vae_approx` 里的 8 个预览用小 VAE 也算进「全部模型」。两边其余文件一致。插件有意不加载 `vae_approx`，因为那不是正常使用的模型。

**Q：插件装好后侧边栏没有「本地模型库」？**

确认目录名和层级：`ComfyUI/custom_nodes/ComfyUI-ModelLibrary/__init__.py` 必须存在；改完后端代码需要重启 ComfyUI，只刷新页面不够。前端改动用 `Ctrl+F5` 即可。

**Q：为什么只能拖 6 种类型？**

这 6 种在 ComfyUI 里有明确的加载节点映射。ControlNet、CLIP Vision、Embedding 暂不自动建节点，避免插错节点或参数。

**Q：删除模型会永久删除吗？**

下载器的删除是「移入回收站」，会把模型文件和同名资料夹一起送进回收站，可从回收站恢复。插件永远不删除文件。

**Q：插件的缩略图缓存可以删吗？**

可以。删除 `comfyui-model-library/cache` 后，下次打开会自动重建。

**Q：以后有 exe 了，目录会变吗？**

不会。exe 仍然放在 `model-downloader/` 下，插件独立放在 `comfyui-model-library/`，二者互不依赖。

---

## 文档

- [人机协作规则](docs/interaction-rules.md)：我们如何提需求、备份、验收和交付。
- [代码备份逻辑与备份节点](docs/backup.md)：快照脚本、里程碑节点、恢复方法。
- [开发计划](docs/roadmap.md)：最终目标、已完成阶段、下一步。

---

## 许可证

本仓库使用 [Mozilla Public License 2.0](LICENSE)。
