import type { Model } from '@/app/components/header/account-setting/model-provider-page/declarations'
import type {
  GraphExtractionConfig,
  GraphRagConfig,
  GraphRetrievalConfig,
} from '@/models/datasets'
import { ModelStatusEnum } from '@/app/components/header/account-setting/model-provider-page/declarations'
import { GRAPH_RAG_LIMITS } from './constants'

export type GraphRagConfigValidationError = 'extract_model_required'
  | 'extract_model_unavailable'
  | 'invalid_graph_limits'
  | 'invalid_temperature'
  | 'invalid_triplets'
  | 'invalid_max_tokens'

export const validateGraphRetrievalConfig = (
  config: GraphRetrievalConfig,
): GraphRagConfigValidationError | undefined => {
  if (config.graph_top_k < GRAPH_RAG_LIMITS.graphTopK.min || config.graph_top_k > GRAPH_RAG_LIMITS.graphTopK.max)
    return 'invalid_graph_limits'
  if (config.graph_max_depth < GRAPH_RAG_LIMITS.graphMaxDepth.min || config.graph_max_depth > GRAPH_RAG_LIMITS.graphMaxDepth.max)
    return 'invalid_graph_limits'
  if (config.graph_timeout_ms < GRAPH_RAG_LIMITS.graphTimeoutMs.min || config.graph_timeout_ms > GRAPH_RAG_LIMITS.graphTimeoutMs.max)
    return 'invalid_graph_limits'
  if (config.graph_weight < GRAPH_RAG_LIMITS.graphWeight.min || config.graph_weight > GRAPH_RAG_LIMITS.graphWeight.max)
    return 'invalid_graph_limits'
  return undefined
}

export const validateGraphExtractionConfig = (
  config: GraphExtractionConfig,
  modelList: Model[],
): GraphRagConfigValidationError | undefined => {
  if (!config.enabled)
    return undefined

  const extractor = config.extract_model_config
  if (!extractor?.provider?.trim() || !extractor.model?.trim())
    return 'extract_model_required'
  if (extractor.temperature < GRAPH_RAG_LIMITS.temperature.min || extractor.temperature > GRAPH_RAG_LIMITS.temperature.max)
    return 'invalid_temperature'
  if (extractor.max_triplets_per_chunk < GRAPH_RAG_LIMITS.maxTripletsPerChunk.min || extractor.max_triplets_per_chunk > GRAPH_RAG_LIMITS.maxTripletsPerChunk.max)
    return 'invalid_triplets'
  if (extractor.max_tokens !== undefined && (extractor.max_tokens < GRAPH_RAG_LIMITS.maxTokens.min || extractor.max_tokens > GRAPH_RAG_LIMITS.maxTokens.max))
    return 'invalid_max_tokens'
  if (modelList.length > 0) {
    const provider = modelList.find(item => item.provider === extractor.provider)
    const model = provider?.models.find(item => item.model === extractor.model && item.status === ModelStatusEnum.active)
    if (!provider || !model)
      return 'extract_model_unavailable'
  }
  return undefined
}

export const validateGraphRagConfig = (
  config: GraphRagConfig,
  modelList: Model[],
): GraphRagConfigValidationError | undefined => {
  const retrievalError = validateGraphRetrievalConfig(config)
  if (retrievalError)
    return retrievalError

  const extractionConfig = {
    enabled: config.enabled,
    extract_model_config: config.extract_model_config ?? undefined,
  }
  const extractionError = validateGraphExtractionConfig(extractionConfig, modelList)
  if (extractionError || !config.extract_model_config)
    return extractionError

  return validateGraphExtractionConfig({ ...extractionConfig, enabled: true }, modelList)
}
