# 代码备份逻辑与备份节点

本文说明两个项目的代码如何备份、备份文件放在哪里、每个节点对应什么阶段，以及如何恢复。

备份只针对**代码和文档**，不包含模型文件、虚拟环境和个人配置。

---

## 1. 备份策略

### 1.1 下载器：里程碑 zip 快照

下载器使用 `model-downloader/tools/snapshot.ps1` 打快照：

```powershell
cd F:\Lora_Downloads\model-downloader
powershell -ExecutionPolicy Bypass -File tools\snapshot.ps1 -Label M3.23
```

执行后生成：

```
model-downloader/_snapshots/model-downloader_20261119-210347_M3.22.zip
```

命名规则：`model-downloader_<年月日-时分秒>_<标签>.zip`。

快照特点：

- 只打包源码、资源、脚本和说明，单个约 0.1–0.2 MB。
- 自动排除：`.venv`、`__pycache__`、`_snapshots`、`.git`、`build`、`dist`、`release`。
- 不包含：模型文件、`%LOCALAPPDATA%\ModelDownloader` 下的配置和任务记录。
- 解压即得到一份可直接运行的源码目录（装好 `.venv` 后）。

### 1.2 插件：Git 版本管理

插件从本次发布起纳入仓库 Git 管理：

- 每次发布打标签，例如 `plugin-v0.1.0`。
- 大改前先提交当前状态，再开分支或打标签。
- 运行产物 `cache/`、`__pycache__/` 不进入版本库。

插件开发阶段（v0.1.0 之前）没有单独的历史快照，本次整理已把最终代码提交到仓库，作为第一个可回滚节点。

### 1.3 发布仓库

本仓库同时保存两个项目的源码与文档，是跨机器的第二份备份。仓库不包含：

- `.venv`、`__pycache__`、`_snapshots`、`cache`、日志；
- 模型文件、示例图、下载任务记录；
- 个人 Token、代理地址等配置。

---

## 2. 下载器备份节点

快照位于 `model-downloader/_snapshots/`，按时间顺序如下（均为 2026-11-18 至 2026-11-19）：

| 快照标签 | 阶段说明 |
| --- | --- |
| `M0-ui` | 初始界面骨架 |
| `M0-ui-v2` | 界面调整第二版 |
| `M0-ui-v3` | 界面调整第三版 |
| `M1-civitai` | 接入 Civitai 下载全链路 |
| `M1.1-naming-overflow` | 文件名/标题溢出修复 |
| `M2-library-scan` | 本地模型库扫描 |
| `M2.1-thumbnails` | 缩略图与示例图展示 |
| `M2.2-scan-both-layouts` | 兼容两种模型目录布局 |
| `M3-library-ux` | 模型库交互完善 |
| `M3.1-context-menu` | 右键菜单 |
| `M3.2-no-hover` | 悬停行为调整 |
| `M3.3-detail-fix` | 详情页修复 |
| `M3.4-doc-clean` | 说明文档整理 |
| `M3.5-copy-link` | 复制链接/信息 |
| `M3.6-scrollbar` | 滚动条样式 |
| `M3.7-list-view` | 列表视图 |
| `M3.8-trae-ui` | 界面风格统一 |
| `M3.9-list-scale` | 列表缩放 |
| `M3.10-statusbar` | 状态栏 |
| `M3.11-custom-titlebar` | 自定义标题栏 |
| `M3.12` | 阶段收尾快照 |
| `M3.13` | 阶段收尾快照 |
| `M3.14` | 阶段收尾快照 |
| `M3.15` | 模型库与下载器功能完善 |
| `M3.16` | 设置页完善 |
| `M3.17` | 缓存清理 |
| `M3.18` | 补数据功能 |
| `M3.19` | 补数据迭代 |
| `M3.20` | 批量操作与删除完善 |
| `M3.21-before-icons` | 替换图标前的保护点 |
| `M3.21` | 新图标与界面收尾 |
| `M3.22` | v0.4.0 收尾节点 |

标签以快照文件名为准；`M3.12`–`M3.14` 等未带描述的快照是阶段性存档，用于回到对应时间点。

---

## 3. 插件备份节点

| 节点 | 说明 |
| --- | --- |
| 设计定稿 | 侧边栏 + 详情弹窗，只读扫描 ComfyUI 模型目录 |
| 首版可用 | 侧边栏、卡片、搜索筛选、详情、示例图、NSFW 模糊 |
| 交互修复 | 修复遮罩层一直盖住面板的问题 |
| 拖拽插入 | 卡片拖到画布创建 Loader 节点 |
| 列表视图 + NSFW 开关 | 增加列表视图；NSFW 改为全局开关 + 详情内切换 |
| 类型收窄 + 打开文件夹 | 拖拽限定 6 类；详情页“复制文件名”改为“打开文件夹” |
| v0.1.0 发布 | 代码、README、文档进入仓库 |

---

## 4. 恢复方法

### 4.1 从 zip 快照恢复下载器

```powershell
cd F:\Lora_Downloads\model-downloader
Expand-Archive "_snapshots\model-downloader_20261119-210347_M3.22.zip" -DestinationPath "$env:TEMP\md_restore" -Force
Copy-Item "$env:TEMP\md_restore\*" . -Recurse -Force
```

恢复前建议先把当前状态另打一个快照，避免覆盖掉还想保留的改动。

`.venv` 不在快照里，恢复后如果依赖未变，可直接继续用原来的 `.venv`；依赖有变化时重新执行：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4.2 从 Git 恢复插件

```powershell
cd F:\Lora_Downloads
git checkout <标签或提交>
```

只恢复插件目录：

```powershell
git checkout <标签或提交> -- comfyui-model-library
```

---

## 5. 备份节奏

- 每个里程碑完成、界面大改前、批量重构前：打一个 zip 快照。
- 插件每次发布：提交并打标签。
- 上传 GitHub 前：确认 `.venv`、`cache`、`_snapshots` 没有被加入仓库。
- 每季度检查一次快照能否正常解压和启动，避免备份损坏。

---

## 6. 注意事项

- `_snapshots` 只存在本机，不进入 Git；换机器时如需保留历史，单独拷贝这个目录。
- 快照不包含模型，恢复代码不会影响模型文件。
- 不要把个人配置（Token、代理）写进代码或文档后再提交。
