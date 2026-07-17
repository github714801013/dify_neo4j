import type { GraphRagConfig } from '@/models/datasets'

export const DEFAULT_GRAPH_RAG_CONFIG: GraphRagConfig = {
  enabled: false,
  query_mode: 'hybrid',
  graph_top_k: 10,
  graph_max_depth: 1,
  graph_timeout_ms: 1500,
  graph_weight: 0.3,
  extract_model_config: null,
  graph_version: 'v1',
}

export const GRAPH_RAG_LIMITS = {
  graphTopK: { min: 1, max: 100 },
  graphMaxDepth: { min: 1, max: 5 },
  graphTimeoutMs: { min: 100, max: 30000 },
  graphWeight: { min: 0, max: 1 },
  temperature: { min: 0, max: 2 },
  maxTripletsPerChunk: { min: 1, max: 50 },
} as const
