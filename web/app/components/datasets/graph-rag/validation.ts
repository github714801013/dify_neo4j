import type { Model } from '@/app/components/header/account-setting/model-provider-page/declarations'
import type { GraphRagConfig } from '@/models/datasets'
import { ModelStatusEnum } from '@/app/components/header/account-setting/model-provider-page/declarations'
import { GRAPH_RAG_LIMITS } from './constants'

export type GraphRagConfigValidationError = 'extract_model_required'
  | 'extract_model_unavailable'
  | 'invalid_graph_limits'
  | 'invalid_temperature'
  | 'invalid_triplets'

export const validateGraphRagConfig = (
  config: GraphRagConfig,
  modelList: Model[],
): GraphRagConfigValidationError | undefined => {
  if (config.graph_top_k < GRAPH_RAG_LIMITS.graphTopK.min || config.graph_top_k > GRAPH_RAG_LIMITS.graphTopK.max)
    return 'invalid_graph_limits'
  if (config.graph_max_depth < GRAPH_RAG_LIMITS.graphMaxDepth.min || config.graph_max_depth > GRAPH_RAG_LIMITS.graphMaxDepth.max)
    return 'invalid_graph_limits'
  if (config.graph_timeout_ms < GRAPH_RAG_LIMITS.graphTimeoutMs.min || config.graph_timeout_ms > GRAPH_RAG_LIMITS.graphTimeoutMs.max)
    return 'invalid_graph_limits'
  if (config.graph_weight < GRAPH_RAG_LIMITS.graphWeight.min || config.graph_weight > GRAPH_RAG_LIMITS.graphWeight.max)
    return 'invalid_graph_limits'

  const extractor = config.extract_model_config
  if (config.enabled && (!extractor?.provider?.trim() || !extractor.model?.trim()))
    return 'extract_model_required'
  if (!extractor)
    return undefined
  if (extractor.temperature < GRAPH_RAG_LIMITS.temperature.min || extractor.temperature > GRAPH_RAG_LIMITS.temperature.max)
    return 'invalid_temperature'
  if (extractor.max_triplets_per_chunk < GRAPH_RAG_LIMITS.maxTripletsPerChunk.min || extractor.max_triplets_per_chunk > GRAPH_RAG_LIMITS.maxTripletsPerChunk.max)
    return 'invalid_triplets'
  if (modelList.length > 0) {
    const provider = modelList.find(item => item.provider === extractor.provider)
    const model = provider?.models.find(item => item.model === extractor.model && item.status === ModelStatusEnum.active)
    if (!provider || !model)
      return 'extract_model_unavailable'
  }
  return undefined
}
