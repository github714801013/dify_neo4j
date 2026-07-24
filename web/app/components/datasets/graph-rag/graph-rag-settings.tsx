'use client'

import type {
  GraphIndexConfig as GraphIndexConfigValue,
  GraphIndexSchema,
} from '@/app/components/workflow/nodes/knowledge-base/types'
import type {
  GraphExtractionConfig,
  GraphRetrievalConfig,
  GraphSchema,
} from '@/models/datasets'
import { Switch } from '@langgenius/dify-ui/switch'
import { useTranslation } from 'react-i18next'
import Divider from '@/app/components/base/divider'
import ParamItem from '@/app/components/base/param-item'
import GraphIndexConfig from '@/app/components/workflow/nodes/knowledge-base/components/graph-index-config'
import { DEFAULT_GRAPH_EXTRACTION_CONFIG } from './constants'

const rowClass = 'flex gap-x-1'
const labelClass = 'flex items-center shrink-0 w-[180px] h-7 pt-1'

const toGraphIndexSchema = (schema?: GraphSchema): GraphIndexSchema | undefined => {
  if (!schema)
    return undefined

  return {
    entity_types: schema.entity_types.map(name => ({
      name,
      properties: schema.entity_properties[name] ?? [],
    })),
    relation_types: schema.relation_types.map(name => ({
      name,
      properties: schema.relation_properties[name] ?? [],
    })),
    allowed_triples: schema.allowed_triples.map(([source_type, relation_type, target_type]) => ({
      source_type,
      relation_type,
      target_type,
    })),
  }
}

const toGraphIndexConfig = (config: GraphExtractionConfig): GraphIndexConfigValue => ({
  enabled: config.enabled,
  schema: toGraphIndexSchema(config.schema),
  extract_model_config: config.extract_model_config,
  graph_version: config.graph_version,
})

const fromGraphIndexConfig = (
  config: GraphIndexConfigValue,
  currentConfig: GraphExtractionConfig,
): GraphExtractionConfig => {
  if (!config.enabled)
    return { ...currentConfig, enabled: false }

  return {
    enabled: true,
    schema: {
      entity_types: config.schema!.entity_types.map(item => item.name),
      relation_types: config.schema!.relation_types.map(item => item.name),
      allowed_triples: config.schema!.allowed_triples.map(item => [
        item.source_type,
        item.relation_type,
        item.target_type,
      ]),
      entity_properties: Object.fromEntries(config.schema!.entity_types.map(item => [item.name, item.properties])),
      relation_properties: Object.fromEntries(config.schema!.relation_types.map(item => [item.name, item.properties])),
    },
    extract_model_config: {
      provider: config.extract_model_config!.provider,
      model: config.extract_model_config!.model,
      temperature: config.extract_model_config!.temperature ?? 0,
      max_tokens: config.extract_model_config!.max_tokens,
      max_triplets_per_chunk: config.extract_model_config!.max_triplets_per_chunk ?? 10,
      strict: config.extract_model_config!.strict ?? true,
    },
    graph_version: config.graph_version,
  }
}

type GraphRagSettingsProps = {
  retrievalConfig: GraphRetrievalConfig
  extractionConfig: GraphExtractionConfig
  onRetrievalConfigChange: (config: GraphRetrievalConfig) => void
  onExtractionConfigChange: (config: GraphExtractionConfig) => void
  readonly?: boolean
}

export function GraphRagSettings({
  retrievalConfig,
  extractionConfig,
  onRetrievalConfigChange,
  onExtractionConfigChange,
  readonly = false,
}: GraphRagSettingsProps) {
  const { t } = useTranslation()
  const extractionTemplate = extractionConfig.schema || extractionConfig.extract_model_config
    ? extractionConfig
    : DEFAULT_GRAPH_EXTRACTION_CONFIG

  return (
    <>
      <Divider type="horizontal" className="my-1 h-px bg-divider-subtle" />
      <div className={rowClass}>
        <div className="flex w-[180px] shrink-0 flex-col">
          <div className="flex h-8 items-center system-sm-semibold text-text-secondary">{t('form.graphRag.title', { ns: 'datasetSettings' })}</div>
          <div className="body-xs-regular text-text-tertiary">{t('form.graphRag.description', { ns: 'datasetSettings' })}</div>
        </div>
        <div className="grow">
          <Switch checked={retrievalConfig.enabled} onCheckedChange={enabled => onRetrievalConfigChange({ ...retrievalConfig, enabled })} disabled={readonly} aria-label={t('form.graphRag.title', { ns: 'datasetSettings' })} />
        </div>
      </div>
      <div className={`${rowClass} ${!retrievalConfig.enabled ? 'opacity-60' : ''}`}>
        <div className={labelClass}><div className="system-sm-semibold text-text-secondary">{t('form.graphRag.queryMode', { ns: 'datasetSettings' })}</div></div>
        <div className="grow">
          <select className="h-8 rounded-lg border border-components-panel-border px-2 text-sm" value={retrievalConfig.query_mode} onChange={event => onRetrievalConfigChange({ ...retrievalConfig, query_mode: event.target.value as GraphRetrievalConfig['query_mode'] })} disabled={readonly}>
            <option value="hybrid">{t('form.graphRag.hybrid', { ns: 'datasetSettings' })}</option>
            <option value="vector">{t('form.graphRag.vector', { ns: 'datasetSettings' })}</option>
          </select>
        </div>
      </div>
      <div className="grid grow grid-cols-1 gap-4 sm:grid-cols-2">
        <ParamItem id="graph_top_k" name={t('form.graphRag.graphTopK', { ns: 'datasetSettings' })} value={retrievalConfig.graph_top_k} min={1} max={100} enable noTooltip disabled={readonly} onChange={(_, graph_top_k) => onRetrievalConfigChange({ ...retrievalConfig, graph_top_k })} />
        <ParamItem id="graph_max_depth" name={t('form.graphRag.maxDepth', { ns: 'datasetSettings' })} value={retrievalConfig.graph_max_depth} min={1} max={5} enable noTooltip disabled={readonly} onChange={(_, graph_max_depth) => onRetrievalConfigChange({ ...retrievalConfig, graph_max_depth })} />
        <ParamItem id="graph_timeout_ms" name={t('form.graphRag.timeout', { ns: 'datasetSettings' })} value={retrievalConfig.graph_timeout_ms} min={100} max={30000} enable noTooltip disabled={readonly} onChange={(_, graph_timeout_ms) => onRetrievalConfigChange({ ...retrievalConfig, graph_timeout_ms })} />
        <ParamItem id="graph_weight" name={t('form.graphRag.weight', { ns: 'datasetSettings' })} value={retrievalConfig.graph_weight} min={0} max={1} step={0.05} enable noTooltip disabled={readonly} onChange={(_, graph_weight) => onRetrievalConfigChange({ ...retrievalConfig, graph_weight })} />
      </div>
      <GraphIndexConfig
        config={extractionConfig.enabled ? toGraphIndexConfig(extractionConfig) : { enabled: false }}
        templateConfig={toGraphIndexConfig(extractionTemplate)}
        onChange={config => onExtractionConfigChange(fromGraphIndexConfig(config, extractionTemplate))}
        readonly={readonly}
      />
    </>
  )
}
