"""ComfyUI 本地模型库。

只读扫描 ComfyUI 自己配置的模型目录（folder_paths），
在侧边栏里浏览模型、示例图与说明，并支持一键插入常用加载节点。

本插件不写、不移动、不删除任何模型文件。
"""

from .backend import routes as _routes  # noqa: F401  导入即注册 HTTP 接口

__version__ = "0.1.3"

WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
