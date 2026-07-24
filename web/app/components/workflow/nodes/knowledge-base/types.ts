import type { IndexingType } from '@/app/components/datasets/create/step-two'
import type { Model, ModelItem } from '@/app/components/header/account-setting/model-provider-page/declarations'
import type { CommonNodeType } from '@/app/components/workflow/types'
import type { RerankingModeEnum, WeightedScoreEnum } from '@/models/datasets'
import type { RETRIEVE_METHOD } from '@/types/app'

export { IndexingType as IndexMethodEnum } from '@/app/components/datasets/create/step-two'
export { WeightedScoreEnum } from '@/models/datasets'
export { RerankingModeEnum as HybridSearchModeEnum } from '@/models/datasets'
export { RETRIEVE_METHOD as RetrievalSearchMethodEnum } from '@/types/app'

export enum ChunkStructureEnum {
  general = 'text_model',
  parent_child = 'hierarchical_model',
  question_answer = 'qa_model',
}

export type RerankingModel = {
  reranking_provider_name: string
  reranking_model_name: string
}

export type WeightedScore = {
  weight_type: WeightedScoreEnum
  vector_setting: {
    vector_weight: number
    embedding_provider_name: string
    embedding_model_name: string
  }
  keyword_setting: {
    keyword_weight: number
  }
}

type RetrievalSetting = {
  search_method?: RETRIEVE_METHOD
  reranking_enable?: boolean
  reranking_model?: RerankingModel
  weights?: WeightedScore
  top_k: number
  score_threshold_enabled: boolean
  score_threshold: number
  reranking_mode?: RerankingModeEnum
}
export type SummaryIndexSetting = {
  enable?: boolean
  model_name?: string
  model_provider_name?: string
  summary_prompt?: string
}
export type GraphPropertyValueType = 'string' | 'number' | 'boolean' | 'date'

export type GraphPropertyDefinition = {
  name: string
  description: string
  value_type: GraphPropertyValueType
  required: boolean
}

export type GraphSchemaType = {
  name: string
  properties: GraphPropertyDefinition[]
}

export type GraphSchemaTriple = {
  source_type: string
  relation_type: string
  target_type: string
}

export type GraphIndexSchema = {
  entity_types: GraphSchemaType[]
  relation_types: GraphSchemaType[]
  allowed_triples: GraphSchemaTriple[]
}

export type GraphIndexConfig = {
  enabled: boolean
  schema?: GraphIndexSchema
  extract_model_config?: {
    provider: string
    model: string
    temperature?: number
    max_tokens?: number
    max_triplets_per_chunk?: number
    strict?: boolean
  }
  graph_version?: string
}

export type KnowledgeBaseNodeType = CommonNodeType & {
  index_chunk_variable_selector: string[]
  chunk_structure?: ChunkStructureEnum
  indexing_technique?: IndexingType
  embedding_model?: string
  embedding_model_provider?: string
  keyword_number: number
  retrieval_model: RetrievalSetting
  _embeddingModelList?: Model[]
  _embeddingProviderModelList?: ModelItem[]
  _rerankModelList?: Model[]
  summary_index_setting?: SummaryIndexSetting
  graph_index_config?: GraphIndexConfig
}
