"""STS2 decision-pack advisor.

STS2_MCP が返す生 state(JSON) を、LLM にそのまま渡すのではなく
「機械的な効果シグネチャ + 計算済みの助言」だけの decision pack に変換する。

LLM 向けの payload は description/title/label 等の自由文を一切含まない
(privacy 境界)。カード名も送らず、id と機械データだけで表現する。
"""
