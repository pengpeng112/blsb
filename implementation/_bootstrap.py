"""仓库引导：把 implementation/、packages/、plugins/ 加入 sys.path。

所有应用入口与测试 conftest 均先 import 本模块，保证
`packages.core_db` / `plugins.domains.pathology` 等设计目录布局可直接 import。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "packages"), os.path.join(_HERE, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

__all__ = ["HERE"]

HERE = _HERE
