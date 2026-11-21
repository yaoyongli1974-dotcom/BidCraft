"""``bidcraft.api`` 兼容层：把包名重定向到 ``bidcraft.api.app``。

历史上 ``bidcraft.api`` 是单文件模块，测试与 CLI 都写 ``from bidcraft.api import ...``，
并且 monkeypatch ``bidcraft.api.<name>``。这里用 ``sys.modules`` 把包对象替换成
``app`` 子模块，使所有导入与 monkeypatch 都落到同一个模块全局上，调用方零改动。
"""

import sys

from . import app as _core

sys.modules[__name__] = _core
