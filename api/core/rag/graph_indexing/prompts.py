"""GraphRAG 实体抽取 Prompt 模板。

设计目标
========

- 约束 LLM 输出结构化 JSON 三元组，便于后续 schema 校验与 Neo4j 写入。
- 仅向 LLM 暴露当前 Dataset 允许的实体类型、关系类型与合法三元组词表，
  从源头降低非法输出比例。
- 模板独立于 LLM 调用实现，便于单独单元测试与后续迭代。

边界
====

- 本模块只构造提示词文本，不调用 LLM、不访问数据库。
- 输出 JSON Schema 固定，解析与校验由 ``extractor.py`` 负责。
"""

from __future__ import annotations

from core.rag.graph.entities import GraphSchema

# 抽取输出 JSON 的固定结构说明，用于在提示词中明确告知 LLM 输出格式。
# entities.name 为实体在原文中的指称；type 必须来自 schema.entity_types。
# relations 的 source/target 必须与 entities.name 一致，type 必须来自
# schema.relation_types 且符合 schema.allowed_triples。
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
    "实体与关系必须严格使用给定词表；不在词表内的内容直接忽略。"
)


def build_user_prompt(segment_text: str, schema: GraphSchema) -> str:
    """根据段落文本与 Graph Schema 构造抽取提示词。

    :param segment_text: 单个段落的内容。
    :param schema: 当前 Dataset 允许的实体/关系词表。
    :return: 完整的用户提示词。
    """
    entity_types = ", ".join(schema.entity_types)
    relation_types = ", ".join(schema.relation_types)
    triples = "; ".join(f"{s}-{r}->{t}" for s, r, t in schema.allowed_triples)
    return (
        "从下方文本中抽取知识图谱实体与关系。\n\n"
        f"允许的实体类型: {entity_types}\n"
        f"允许的关系类型: {relation_types}\n"
        f"允许的三元组组合(源实体类型-关系-目标实体类型): {triples}\n\n"
        "要求:\n"
        "1. 实体 name 使用原文出现的指称，保持原文语言。\n"
        "2. 只输出类型属于允许集合、且三元组组合合法的实体与关系。\n"
        "3. source 与 target 的 name 必须在 entities 中出现过。\n"
        "4. 若文本无可抽取内容，输出空数组。\n\n"
        f"输出 JSON 结构(仅输出该 JSON):\n{_EXTRACTION_OUTPUT_SPEC}\n\n"
        f"文本:\n{segment_text}"
    )


__all__ = ["_SYSTEM_PROMPT", "build_user_prompt"]