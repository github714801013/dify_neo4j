import type {
  GraphExtractionConfig,
  GraphRagConfig,
  GraphRetrievalConfig,
  GraphSchema,
} from '@/models/datasets'

export const DEFAULT_GRAPH_RETRIEVAL_CONFIG: GraphRetrievalConfig = {
  enabled: false,
  query_mode: 'hybrid',
  graph_top_k: 10,
  graph_max_depth: 1,
  graph_timeout_ms: 1500,
  graph_weight: 0.3,
}

export const DEFAULT_DOCUMENT_GRAPH_SCHEMA: GraphSchema = {
  entity_types: ['product', 'module', 'feature', 'version', 'api', 'parameter', 'error', 'document'],
  relation_types: ['contains', 'supports', 'depends_on', 'available_in', 'configures', 'calls', 'returns', 'causes', 'solves', 'describes'],
  allowed_triples: [
    ['product', 'contains', 'module'],
    ['module', 'contains', 'feature'],
    ['feature', 'available_in', 'version'],
    ['feature', 'configures', 'parameter'],
    ['api', 'calls', 'api'],
    ['error', 'causes', 'feature'],
    ['error', 'solves', 'feature'],
    ['document', 'describes', 'product'],
    ['document', 'describes', 'module'],
    ['document', 'describes', 'feature'],
  ],
  entity_properties: {},
  relation_properties: {},
}

export const DEFAULT_GRAPH_EXTRACTION_CONFIG: GraphExtractionConfig = {
  enabled: false,
  schema: DEFAULT_DOCUMENT_GRAPH_SCHEMA,
}

export const DEFAULT_GRAPH_RAG_CONFIG: GraphRagConfig = {
  ...DEFAULT_GRAPH_RETRIEVAL_CONFIG,
  extract_model_config: null,
  graph_version: 'v1',
}

export const GRAPH_RAG_LIMITS = {
  graphTopK: { min: 1, max: 100 },
  graphMaxDepth: { min: 1, max: 5 },
  graphTimeoutMs: { min: 100, max: 30000 },
  graphWeight: { min: 0, max: 1 },
  temperature: { min: 0, max: 2 },
  maxTokens: { min: 1, max: 131072 },
  maxTripletsPerChunk: { min: 1, max: 50 },
} as const
