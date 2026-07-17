'use client'

import type { Model } from '@/app/components/header/account-setting/model-provider-page/declarations'
import type { GraphRagConfig } from '@/models/datasets'
import { Switch } from '@langgenius/dify-ui/switch'
import { useTranslation } from 'react-i18next'
import Divider from '@/app/components/base/divider'
import ParamItem from '@/app/components/base/param-item'
import ModelSelector from '@/app/components/header/account-setting/model-provider-page/model-selector'
import { GRAPH_RAG_LIMITS } from './constants'

const rowClass = 'flex gap-x-1'
const labelClass = 'flex items-center shrink-0 w-[180px] h-7 pt-1'

type GraphRagSettingsProps = {
  config: GraphRagConfig
  modelList: Model[]
  onChange: (config: GraphRagConfig) => void
  readonly?: boolean
}

export function GraphRagSettings({ config, modelList, onChange, readonly = false }: GraphRagSettingsProps) {
  const { t } = useTranslation()
  const extractModelConfig = config.extract_model_config
  const updateExtractor = (patch: Partial<NonNullable<GraphRagConfig['extract_model_config']>>) => {
    onChange({
      ...config,
      extract_model_config: {
        provider: extractModelConfig?.provider ?? '',
        model: extractModelConfig?.model ?? '',
        temperature: extractModelConfig?.temperature ?? 0,
        max_tokens: extractModelConfig?.max_tokens,
        max_triplets_per_chunk: extractModelConfig?.max_triplets_per_chunk ?? 10,
        strict: extractModelConfig?.strict ?? true,
        ...patch,
      },
    })
  }

  return (
    <>
      <Divider type="horizontal" className="my-1 h-px bg-divider-subtle" />
      <div className={rowClass}>
        <div className="flex w-[180px] shrink-0 flex-col">
          <div className="flex h-8 items-center system-sm-semibold text-text-secondary">{t('form.graphRag.title', { ns: 'datasetSettings' })}</div>
          <div className="body-xs-regular text-text-tertiary">{t('form.graphRag.description', { ns: 'datasetSettings' })}</div>
        </div>
        <div className="grow">
          <Switch checked={config.enabled} onCheckedChange={enabled => onChange({ ...config, enabled })} disabled={readonly} aria-label={t('form.graphRag.title', { ns: 'datasetSettings' })} />
        </div>
      </div>
      <div className={`${rowClass} ${!config.enabled ? 'opacity-60' : ''}`}>
        <div className={labelClass}><div className="system-sm-semibold text-text-secondary">{t('form.graphRag.queryMode', { ns: 'datasetSettings' })}</div></div>
        <div className="grow">
          <select className="h-8 rounded-lg border border-components-panel-border px-2 text-sm" value={config.query_mode} onChange={event => onChange({ ...config, query_mode: event.target.value as GraphRagConfig['query_mode'] })} disabled={readonly}>
            <option value="hybrid">{t('form.graphRag.hybrid', { ns: 'datasetSettings' })}</option>
            <option value="vector">{t('form.graphRag.vector', { ns: 'datasetSettings' })}</option>
          </select>
        </div>
      </div>
      <div className="grid grow grid-cols-1 gap-4 sm:grid-cols-2">
        <ParamItem
          id="graph_top_k"
          name={t('form.graphRag.graphTopK', { ns: 'datasetSettings' })}
          value={config.graph_top_k}
          min={1}
          max={100}
          enable={true}
          noTooltip
          disabled={readonly}
          onChange={(_, graph_top_k) => onChange({ ...config, graph_top_k })}
        />
        <ParamItem
          id="graph_max_depth"
          name={t('form.graphRag.maxDepth', { ns: 'datasetSettings' })}
          value={config.graph_max_depth}
          min={1}
          max={5}
          enable={true}
          noTooltip
          disabled={readonly}
          onChange={(_, graph_max_depth) => onChange({ ...config, graph_max_depth })}
        />
        <ParamItem
          id="graph_timeout_ms"
          name={t('form.graphRag.timeout', { ns: 'datasetSettings' })}
          value={config.graph_timeout_ms}
          min={100}
          max={30000}
          enable={true}
          noTooltip
          disabled={readonly}
          onChange={(_, graph_timeout_ms) => onChange({ ...config, graph_timeout_ms })}
        />
        <ParamItem
          id="graph_weight"
          name={t('form.graphRag.weight', { ns: 'datasetSettings' })}
          value={config.graph_weight}
          min={0}
          max={1}
          step={0.05}
          enable={true}
          noTooltip
          disabled={readonly}
          onChange={(_, graph_weight) => onChange({ ...config, graph_weight })}
        />
      </div>
      <div className={rowClass}>
        <div className="flex w-[180px] shrink-0 flex-col">
          <div className="system-sm-semibold text-text-secondary">{t('form.graphRag.extractModel', { ns: 'datasetSettings' })}</div>
          <div className="body-xs-regular text-text-tertiary">{t('form.graphRag.extractModelDescription', { ns: 'datasetSettings' })}</div>
        </div>
        <div className="grid grow grid-cols-2 gap-2">
          <div className="col-span-2">
            <ModelSelector
              defaultModel={extractModelConfig?.provider && extractModelConfig.model
                ? { provider: extractModelConfig.provider, model: extractModelConfig.model }
                : undefined}
              modelList={modelList}
              onSelect={model => updateExtractor({ provider: model.provider, model: model.model })}
              readonly={readonly}
            />
          </div>
          <ParamItem
            id="temperature"
            name={t('form.graphRag.temperature', { ns: 'datasetSettings' })}
            value={extractModelConfig?.temperature ?? 0}
            min={0}
            max={2}
            step={0.1}
            enable={true}
            noTooltip
            disabled={readonly}
            onChange={(_, temperature) => updateExtractor({ temperature })}
          />
          <ParamItem
            id="max_triplets_per_chunk"
            name={t('form.graphRag.maxTriplets', { ns: 'datasetSettings' })}
            value={extractModelConfig?.max_triplets_per_chunk ?? 10}
            min={1}
            max={50}
            enable={true}
            noTooltip
            disabled={readonly}
            onChange={(_, max_triplets_per_chunk) => updateExtractor({ max_triplets_per_chunk })}
          />
          <ParamItem
            id="max_tokens"
            name={t('form.graphRag.maxTokens', { ns: 'datasetSettings' })}
            value={extractModelConfig?.max_tokens ?? 0}
            min={GRAPH_RAG_LIMITS.maxTokens.min}
            max={GRAPH_RAG_LIMITS.maxTokens.max}
            enable={true}
            tip={t('form.graphRag.maxTokensHelp', { ns: 'datasetSettings' })}
            disabled={readonly}
            onChange={(_, max_tokens) => updateExtractor({ max_tokens: max_tokens || undefined })}
          />
          <label className="col-span-2 flex items-center gap-2 text-sm text-text-secondary">
            <Switch checked={extractModelConfig?.strict ?? true} onCheckedChange={strict => updateExtractor({ strict })} disabled={readonly} />
            {t('form.graphRag.strictSchema', { ns: 'datasetSettings' })}
          </label>
        </div>
      </div>
    </>
  )
}
