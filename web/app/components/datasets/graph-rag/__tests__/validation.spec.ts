import type { Model } from '@/app/components/header/account-setting/model-provider-page/declarations'
import type { GraphRagConfig } from '@/models/datasets'
import { ConfigurationMethodEnum, ModelStatusEnum, ModelTypeEnum } from '@/app/components/header/account-setting/model-provider-page/declarations'
import { validateGraphRagConfig } from '../validation'

const baseConfig: GraphRagConfig = {
  enabled: false,
  query_mode: 'hybrid',
  graph_top_k: 10,
  graph_max_depth: 1,
  graph_timeout_ms: 1500,
  graph_weight: 0.3,
  extract_model_config: null,
  graph_version: 'v1',
}

const modelList: Model[] = [{
  provider: 'openai',
  icon_small: { en_US: '', zh_Hans: '' },
  label: { en_US: 'OpenAI', zh_Hans: 'OpenAI' },
  status: ModelStatusEnum.active,
  models: [{
    model: 'gpt-4o-mini',
    label: { en_US: 'GPT-4o mini', zh_Hans: 'GPT-4o mini' },
    model_type: ModelTypeEnum.textGeneration,
    fetch_from: ConfigurationMethodEnum.predefinedModel,
    status: ModelStatusEnum.active,
    model_properties: {},
    load_balancing_enabled: false,
  }],
}]

describe('validateGraphRagConfig', () => {
  it('accepts a disabled default configuration', () => {
    expect(validateGraphRagConfig(baseConfig, modelList)).toBeUndefined()
  })

  it('requires an extractor model when enabled', () => {
    expect(validateGraphRagConfig({ ...baseConfig, enabled: true }, modelList)).toBe('extract_model_required')
  })

  it('rejects a model that is not configured in Dify', () => {
    expect(validateGraphRagConfig({
      ...baseConfig,
      enabled: true,
      extract_model_config: {
        provider: 'unknown',
        model: 'model',
        temperature: 0,
        max_triplets_per_chunk: 10,
        strict: true,
      },
    }, modelList)).toBe('extract_model_unavailable')
  })

  it('rejects out-of-range graph extraction settings', () => {
    expect(validateGraphRagConfig({
      ...baseConfig,
      extract_model_config: {
        provider: 'openai',
        model: 'gpt-4o-mini',
        temperature: 2.1,
        max_triplets_per_chunk: 10,
        strict: true,
      },
    }, modelList)).toBe('invalid_temperature')
  })
})
