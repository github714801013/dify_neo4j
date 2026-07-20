"""GraphRAG 实体关系抽取 Prompt 模板。

模板只描述允许的实体/关系、strict 语义和单段落三元组上限，不调用模型或
访问数据库。运行时仍由 ``extractor.py`` 做最终 Schema 校验和数量限制。
"""

from __future__ import annotations

from core.rag.graph.entities import GraphSchema

_EXTRACTION_OUTPUT_SPEC = """{
  "entities": [
    {"name": "<entity surface form>", "type": "<one of allowed entity types>"}
  ],
  "relations": [
    {
      "source": "<entity name>",
      "type": "<one of allowed relation types>",
      "target": "<entity name>"
    }
  ]
}"""

_SYSTEM_PROMPT = (
    "你是一个严谨的知识图谱实体关系抽取助手。只输出一个合法 JSON 对象，"
    "不要输出任何解释、注释或 Markdown 代码块标记。"
    "实体与关系必须使用给定词表；不在词表内的内容直接忽略。"
)


def build_user_prompt(
    segment_text: str,
    schema: GraphSchema,
    *,
    strict: bool = True,
    max_triplets_per_chunk: int = 10,
) -> str:
    """根据段落、Schema 和抽取约束构造用户提示词。"""
    entity_types = ", ".join(schema.entity_types)
    relation_types = ", ".join(schema.relation_types)
    triples = "; ".join(f"{source}-{relation}->{target}" for source, relation, target in schema.allowed_triples)
    triple_rule = (
        "关系的源类型、关系类型和目标类型必须命中允许的三元组组合。"
        if strict
        else "源实体类型、关系类型和目标实体类型必须分别来自允许词表；允许词表内的非模板组合可以保留。"
    )
    return (
        "从下方文本中抽取知识图谱实体与关系。\n\n"
        f"允许的实体类型: {entity_types}\n"
        f"允许的关系类型: {relation_types}\n"
        f"允许的三元组组合(源实体类型-关系-目标实体类型): {triples}\n\n"
        "要求:\n"
        "1. 实体 name 使用原文出现的指称，保持原文语言。\n"
        "2. 只输出实体类型和关系类型属于允许词表的内容。\n"
        f"3. {triple_rule}\n"
        "4. source 与 target 的 name 必须在 entities 中出现过。\n"
        f"5. relations 最多输出 {max_triplets_per_chunk} 条，优先保留信息明确且重要的事实。\n"
        "6. 若文本无可抽取内容，输出空数组。\n\n"
        f"输出 JSON 结构(仅输出该 JSON):\n{_EXTRACTION_OUTPUT_SPEC}\n\n"
        f"文本:\n{segment_text}"
    )


__all__ = ["_SYSTEM_PROMPT", "build_user_prompt"]
