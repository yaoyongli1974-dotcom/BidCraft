"""多维查重（对标易标第二批，v0.25.0）。

四个维度各自独立、只读章节正文：

* :mod:`bidcraft.similarity.text` —— 段落级文本查重（复用 ``dedup``）。
* :mod:`bidcraft.similarity.structure` —— 标题 / 章节顺序 / 大纲结构。
* :mod:`bidcraft.similarity.semantic` —— 向量语义相似（不可用时回退文本）。
* :mod:`bidcraft.similarity.image` —— 图片哈希 / 感知哈希候选。

统一编排在 :mod:`bidcraft.similarity.service`。
"""

from . import image, semantic, service, structure, text

__all__ = ["image", "semantic", "service", "structure", "text"]
