import type {
  GraphIndexConfig as GraphIndexConfigValue,
  GraphIndexSchema,
  GraphPropertyDefinition,
  GraphSchemaTriple,
  GraphSchemaType,
} from '../../types'
import type { DefaultModel } from '@/app/components/header/account-setting/model-provider-page/declarations'
import { Button } from '@langgenius/dify-ui/button'
import { Input } from '@langgenius/dify-ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectTrigger,
} from '@langgenius/dify-ui/select'
import { Switch } from '@langgenius/dify-ui/switch'
import {
  useCallback,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'
import { ModelTypeEnum } from '@/app/components/header/account-setting/model-provider-page/declarations'
import { useModelList } from '@/app/components/header/account-setting/model-provider-page/hooks'
import ModelSelector from '@/app/components/header/account-setting/model-provider-page/model-selector'

type GraphIndexConfigProps = {
  config?: GraphIndexConfigValue
  onChange: (config: GraphIndexConfigValue) => void
  readonly?: boolean
}

type DraftGraphPropertyDefinition = GraphPropertyDefinition & {
  id: string
}

type DraftGraphSchemaType = Omit<GraphSchemaType, 'properties'> & {
  id: string
  properties: DraftGraphPropertyDefinition[]
}

type DraftGraphSchemaTriple = GraphSchemaTriple & {
  id: string
}

type GraphIndexDraft = {
  enabled: boolean
  schema: {
    entity_types: DraftGraphSchemaType[]
    relation_types: DraftGraphSchemaType[]
    allowed_triples: DraftGraphSchemaTriple[]
  }
  extractModelConfig?: GraphIndexConfigValue['extract_model_config']
}

let draftItemSequence = 0

const createDraftItemId = () => `graph-index-item-${draftItemSequence++}`

const graphPropertyNamePattern = /^[a-z][a-z0-9_]{0,63}$/

const graphPropertyValueTypes = ['string', 'number', 'boolean', 'date'] as const

const createPropertyDefinition = (): DraftGraphPropertyDefinition => ({
  id: createDraftItemId(),
  name: 'property',
  description: '',
  value_type: 'string',
  required: false,
})

const createSchemaType = (name: string, properties: GraphPropertyDefinition[] = []): GraphSchemaType => ({
  name,
  properties: properties.map(property => ({ ...property })),
})

const createDraftSchemaType = (name: string, properties: GraphPropertyDefinition[] = []): DraftGraphSchemaType => ({
  name,
  id: createDraftItemId(),
  properties: properties.map(property => ({ ...property, id: createDraftItemId() })),
})

const normalizeSchema = (schema?: GraphIndexSchema): GraphIndexDraft['schema'] => ({
  entity_types: schema?.entity_types.map(item => createDraftSchemaType(item.name, item.properties)) ?? [],
  relation_types: schema?.relation_types.map(item => createDraftSchemaType(item.name)) ?? [],
  allowed_triples: schema?.allowed_triples.map(item => ({ ...item, id: createDraftItemId() })) ?? [],
})

const createDraft = (config?: GraphIndexConfigValue): GraphIndexDraft => ({
  enabled: config?.enabled === true,
  schema: normalizeSchema(config?.schema),
  extractModelConfig: config?.extract_model_config
    ? {
        provider: config.extract_model_config.provider,
        model: config.extract_model_config.model,
      }
    : undefined,
})

const getConfigKey = (config?: GraphIndexConfigValue) => JSON.stringify(config ?? { enabled: false })

const getNextTypeName = (prefix: string, types: GraphSchemaType[]) => {
  const names = new Set(types.map(item => item.name.trim()))
  let index = 1
  let candidate = prefix
  while (names.has(candidate)) {
    index += 1
    candidate = `${prefix}_${index}`
  }
  return candidate
}

const hasUniqueNonBlankNames = (types: GraphSchemaType[]) => {
  const names = types.map(item => item.name.trim())
  return names.every(Boolean) && names.length === new Set(names).size
}

const isEntityPropertyValid = (property: GraphPropertyDefinition) => {
  return graphPropertyNamePattern.test(property.name.trim())
    && !!property.description.trim()
    && graphPropertyValueTypes.includes(property.value_type)
    && typeof property.required === 'boolean'
}

const hasValidEntityProperties = (entityType: GraphSchemaType) => {
  const propertyNames = entityType.properties.map(property => property.name.trim())
  return entityType.properties.every(isEntityPropertyValid)
    && propertyNames.length === new Set(propertyNames).size
}

const isSchemaComplete = (schema: GraphIndexDraft['schema']) => {
  if (!schema.entity_types.length || !schema.relation_types.length || !schema.allowed_triples.length)
    return false
  if (!hasUniqueNonBlankNames(schema.entity_types) || !hasUniqueNonBlankNames(schema.relation_types))
    return false
  if (!schema.entity_types.every(hasValidEntityProperties))
    return false

  const entityTypes = new Set(schema.entity_types.map(item => item.name.trim()))
  const relationTypes = new Set(schema.relation_types.map(item => item.name.trim()))
  const triples = schema.allowed_triples.map(item => [
    item.source_type.trim(),
    item.relation_type.trim(),
    item.target_type.trim(),
  ])

  return triples.every(([sourceType, relationType, targetType]) => (
    !!sourceType
    && !!relationType
    && !!targetType
    && entityTypes.has(sourceType)
    && relationTypes.has(relationType)
    && entityTypes.has(targetType)
  )) && triples.length === new Set(triples.map(item => JSON.stringify(item))).size
}

const isDraftComplete = (draft: GraphIndexDraft) => {
  return draft.enabled
    && !!draft.extractModelConfig?.provider.trim()
    && !!draft.extractModelConfig?.model.trim()
    && isSchemaComplete(draft.schema)
}

const toConfig = (draft: GraphIndexDraft): GraphIndexConfigValue => ({
  enabled: true,
  schema: {
    entity_types: draft.schema.entity_types.map(item => createSchemaType(
      item.name.trim(),
      item.properties.map(property => ({
        name: property.name.trim(),
        description: property.description.trim(),
        value_type: property.value_type,
        required: property.required,
      })),
    )),
    relation_types: draft.schema.relation_types.map(item => createSchemaType(item.name.trim())),
    allowed_triples: draft.schema.allowed_triples.map(item => ({
      source_type: item.source_type.trim(),
      relation_type: item.relation_type.trim(),
      target_type: item.target_type.trim(),
    })),
  },
  extract_model_config: {
    provider: draft.extractModelConfig!.provider.trim(),
    model: draft.extractModelConfig!.model.trim(),
  },
})

const GraphIndexConfigContent = ({
  config,
  onChange,
  readonly = false,
}: GraphIndexConfigProps) => {
  const { t } = useTranslation()
  const { data: modelList } = useModelList(ModelTypeEnum.textGeneration)
  const [draft, setDraft] = useState<GraphIndexDraft>(() => createDraft(config))
  const draftRef = useRef(draft)

  const updateDraft = useCallback((updater: (current: GraphIndexDraft) => GraphIndexDraft) => {
    const nextDraft = updater(draftRef.current)
    draftRef.current = nextDraft
    setDraft(nextDraft)

    if (isDraftComplete(nextDraft))
      onChange(toConfig(nextDraft))
  }, [onChange])

  const handleEnabledChange = useCallback((enabled: boolean) => {
    if (!enabled) {
      const nextDraft = {
        ...draftRef.current,
        enabled: false,
      }
      draftRef.current = nextDraft
      setDraft(nextDraft)
      onChange({ enabled: false })
      return
    }

    updateDraft(current => ({
      ...current,
      enabled: true,
    }))
  }, [onChange, updateDraft])

  const updateSchema = useCallback((updater: (schema: GraphIndexDraft['schema']) => GraphIndexDraft['schema']) => {
    updateDraft(current => ({
      ...current,
      schema: updater(current.schema),
    }))
  }, [updateDraft])

  const handleModelChange = useCallback((model: DefaultModel) => {
    updateDraft(current => ({
      ...current,
      extractModelConfig: {
        provider: model.provider,
        model: model.model,
      },
    }))
  }, [updateDraft])

  const model = useMemo(() => {
    if (!draft.extractModelConfig?.provider || !draft.extractModelConfig.model)
      return undefined

    return {
      provider: draft.extractModelConfig.provider,
      model: draft.extractModelConfig.model,
    }
  }, [draft.extractModelConfig])

  const canAddTriple = draft.schema.entity_types.length > 0 && draft.schema.relation_types.length > 0
  const isIncomplete = draft.enabled && !isDraftComplete(draft)

  const updateTypeName = useCallback((kind: 'entity_types' | 'relation_types', index: number, name: string) => {
    updateSchema(schema => ({
      ...schema,
      [kind]: schema[kind].map((item, itemIndex) => (
        itemIndex === index ? { ...item, name } : item
      )),
    }))
  }, [updateSchema])

  const removeType = useCallback((kind: 'entity_types' | 'relation_types', index: number) => {
    updateSchema((schema) => {
      const removedType = schema[kind][index]?.name
      const nextTypes = schema[kind].filter((_, itemIndex) => itemIndex !== index)
      const allowedTriples = kind === 'entity_types'
        ? schema.allowed_triples.filter(item => item.source_type !== removedType && item.target_type !== removedType)
        : schema.allowed_triples.filter(item => item.relation_type !== removedType)

      return {
        ...schema,
        [kind]: nextTypes,
        allowed_triples: allowedTriples,
      }
    })
  }, [updateSchema])

  const updateEntityProperty = useCallback((entityIndex: number, propertyIndex: number, updater: (property: DraftGraphPropertyDefinition) => DraftGraphPropertyDefinition) => {
    updateSchema(schema => ({
      ...schema,
      entity_types: schema.entity_types.map((entityType, currentEntityIndex) => (
        currentEntityIndex === entityIndex
          ? {
              ...entityType,
              properties: entityType.properties.map((property, currentPropertyIndex) => (
                currentPropertyIndex === propertyIndex ? updater(property) : property
              )),
            }
          : entityType
      )),
    }))
  }, [updateSchema])

  const updateTriple = useCallback((index: number, updater: (triple: DraftGraphSchemaTriple) => DraftGraphSchemaTriple) => {
    updateSchema(schema => ({
      ...schema,
      allowed_triples: schema.allowed_triples.map((item, itemIndex) => (
        itemIndex === index ? updater(item) : item
      )),
    }))
  }, [updateSchema])

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="system-sm-medium text-text-secondary">
            {t('nodes.knowledgeBase.graphIndex.title', { ns: 'workflow' })}
          </div>
          <div className="body-xs-regular text-text-tertiary">
            {t('nodes.knowledgeBase.graphIndex.description', { ns: 'workflow' })}
          </div>
        </div>
        <Switch
          aria-label={t('nodes.knowledgeBase.graphIndex.title', { ns: 'workflow' })}
          checked={draft.enabled}
          onCheckedChange={handleEnabledChange}
          disabled={readonly}
        />
      </div>
      {draft.enabled && (
        <div className="space-y-4 rounded-lg border border-divider-regular bg-components-panel-bg p-3">
          {isIncomplete && (
            <div role="status" className="body-xs-regular text-text-warning">
              {t('nodes.knowledgeBase.graphIndex.incomplete', { ns: 'workflow' })}
            </div>
          )}
          <div className="space-y-1">
            <div className="system-sm-medium text-text-secondary">
              {t('nodes.knowledgeBase.graphIndex.extractModel', { ns: 'workflow' })}
            </div>
            <ModelSelector
              defaultModel={model}
              modelList={modelList}
              onSelect={handleModelChange}
              readonly={readonly}
              showDeprecatedWarnIcon
            />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="system-sm-medium text-text-secondary">
                {t('nodes.knowledgeBase.graphIndex.entityTypes', { ns: 'workflow' })}
              </div>
              <Button
                size="small"
                disabled={readonly}
                onClick={() => updateSchema(schema => ({
                  ...schema,
                  entity_types: [...schema.entity_types, createDraftSchemaType(getNextTypeName('ENTITY', schema.entity_types))],
                }))}
              >
                {t('nodes.knowledgeBase.graphIndex.addEntityType', { ns: 'workflow' })}
              </Button>
            </div>
            <div className="space-y-3">
              {draft.schema.entity_types.map((item, index) => (
                <div key={item.id} className="space-y-2 rounded-md border border-divider-regular p-2">
                  <div className="flex gap-2">
                    <Input
                      aria-label={t('nodes.knowledgeBase.graphIndex.entityTypes', { ns: 'workflow' })}
                      value={item.name}
                      onChange={event => updateTypeName('entity_types', index, event.target.value)}
                      disabled={readonly}
                    />
                    <Button
                      size="small"
                      tone="destructive"
                      variant="ghost"
                      disabled={readonly}
                      onClick={() => removeType('entity_types', index)}
                    >
                      {t('operation.delete', { ns: 'common' })}
                    </Button>
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="system-xs-medium text-text-secondary">
                        {t('nodes.knowledgeBase.graphIndex.entityProperties', { ns: 'workflow' })}
                      </div>
                      <Button
                        size="small"
                        disabled={readonly}
                        onClick={() => updateSchema(schema => ({
                          ...schema,
                          entity_types: schema.entity_types.map((entityType, entityIndex) => (
                            entityIndex === index
                              ? { ...entityType, properties: [...entityType.properties, createPropertyDefinition()] }
                              : entityType
                          )),
                        }))}
                      >
                        {t('nodes.knowledgeBase.graphIndex.addEntityProperty', { ns: 'workflow' })}
                      </Button>
                    </div>
                    {item.properties.map((property, propertyIndex) => (
                      <div key={property.id} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto_auto] items-center gap-2">
                        <Input
                          aria-label={t('nodes.knowledgeBase.graphIndex.propertyName', { ns: 'workflow' })}
                          value={property.name}
                          onChange={event => updateEntityProperty(index, propertyIndex, current => ({ ...current, name: event.target.value }))}
                          disabled={readonly}
                        />
                        <Input
                          aria-label={t('nodes.knowledgeBase.graphIndex.propertyDescription', { ns: 'workflow' })}
                          value={property.description}
                          onChange={event => updateEntityProperty(index, propertyIndex, current => ({ ...current, description: event.target.value }))}
                          disabled={readonly}
                        />
                        <select
                          aria-label={t('nodes.knowledgeBase.graphIndex.propertyValueType', { ns: 'workflow' })}
                          className="h-8 rounded-md border border-divider-regular bg-components-panel-bg px-2 text-sm"
                          value={property.value_type}
                          disabled={readonly}
                          onChange={event => updateEntityProperty(index, propertyIndex, current => ({
                            ...current,
                            value_type: event.target.value as GraphPropertyDefinition['value_type'],
                          }))}
                        >
                          {graphPropertyValueTypes.map(valueType => (
                            <option key={valueType} value={valueType}>
                              {t(`nodes.knowledgeBase.graphIndex.propertyValueTypes.${valueType}`, { ns: 'workflow' })}
                            </option>
                          ))}
                        </select>
                        <Switch
                          aria-label={t('nodes.knowledgeBase.graphIndex.propertyRequired', { ns: 'workflow' })}
                          checked={property.required}
                          onCheckedChange={required => updateEntityProperty(index, propertyIndex, current => ({ ...current, required }))}
                          disabled={readonly}
                        />
                        <Button
                          aria-label={t('nodes.knowledgeBase.graphIndex.deleteEntityProperty', { ns: 'workflow' })}
                          size="small"
                          tone="destructive"
                          variant="ghost"
                          disabled={readonly}
                          onClick={() => updateSchema(schema => ({
                            ...schema,
                            entity_types: schema.entity_types.map((entityType, entityIndex) => (
                              entityIndex === index
                                ? {
                                    ...entityType,
                                    properties: entityType.properties.filter((_, currentPropertyIndex) => currentPropertyIndex !== propertyIndex),
                                  }
                                : entityType
                            )),
                          }))}
                        >
                          {t('operation.delete', { ns: 'common' })}
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="system-sm-medium text-text-secondary">
                {t('nodes.knowledgeBase.graphIndex.relationTypes', { ns: 'workflow' })}
              </div>
              <Button
                size="small"
                disabled={readonly}
                onClick={() => updateSchema(schema => ({
                  ...schema,
                  relation_types: [...schema.relation_types, createDraftSchemaType(getNextTypeName('RELATION', schema.relation_types))],
                }))}
              >
                {t('nodes.knowledgeBase.graphIndex.addRelationType', { ns: 'workflow' })}
              </Button>
            </div>
            <div className="space-y-2">
              {draft.schema.relation_types.map((item, index) => (
                <div key={item.id} className="flex gap-2">
                  <Input
                    aria-label={t('nodes.knowledgeBase.graphIndex.relationTypes', { ns: 'workflow' })}
                    value={item.name}
                    onChange={event => updateTypeName('relation_types', index, event.target.value)}
                    disabled={readonly}
                  />
                  <Button
                    size="small"
                    tone="destructive"
                    variant="ghost"
                    disabled={readonly}
                    onClick={() => removeType('relation_types', index)}
                  >
                    {t('operation.delete', { ns: 'common' })}
                  </Button>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="system-sm-medium text-text-secondary">
                {t('nodes.knowledgeBase.graphIndex.allowedTriples', { ns: 'workflow' })}
              </div>
              <Button
                size="small"
                disabled={readonly || !canAddTriple}
                onClick={() => updateSchema(schema => ({
                  ...schema,
                  allowed_triples: [
                    ...schema.allowed_triples,
                    {
                      id: createDraftItemId(),
                      source_type: schema.entity_types[0]!.name,
                      relation_type: schema.relation_types[0]!.name,
                      target_type: schema.entity_types[0]!.name,
                    },
                  ],
                }))}
              >
                {t('nodes.knowledgeBase.graphIndex.addAllowedTriple', { ns: 'workflow' })}
              </Button>
            </div>
            <div className="space-y-2">
              {draft.schema.allowed_triples.map((triple, index) => (
                <div key={triple.id} data-testid={`graph-index-triple-${index}`} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2">
                  <Select
                    value={triple.source_type || null}
                    disabled={readonly}
                    onValueChange={value => updateTriple(index, current => ({
                      ...current,
                      source_type: value ?? '',
                    }))}
                  >
                    <SelectTrigger aria-label={t('nodes.knowledgeBase.graphIndex.sourceType', { ns: 'workflow' })} className="w-full" disabled={readonly}>
                      {triple.source_type}
                    </SelectTrigger>
                    <SelectContent>
                      {draft.schema.entity_types.map(item => (
                        <SelectItem key={item.id} value={item.name}>
                          <SelectItemText>{item.name}</SelectItemText>
                          <SelectItemIndicator />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={triple.relation_type || null}
                    disabled={readonly}
                    onValueChange={value => updateTriple(index, current => ({
                      ...current,
                      relation_type: value ?? '',
                    }))}
                  >
                    <SelectTrigger aria-label={t('nodes.knowledgeBase.graphIndex.relationType', { ns: 'workflow' })} className="w-full" disabled={readonly}>
                      {triple.relation_type}
                    </SelectTrigger>
                    <SelectContent>
                      {draft.schema.relation_types.map(item => (
                        <SelectItem key={item.id} value={item.name}>
                          <SelectItemText>{item.name}</SelectItemText>
                          <SelectItemIndicator />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={triple.target_type || null}
                    disabled={readonly}
                    onValueChange={value => updateTriple(index, current => ({
                      ...current,
                      target_type: value ?? '',
                    }))}
                  >
                    <SelectTrigger aria-label={t('nodes.knowledgeBase.graphIndex.targetType', { ns: 'workflow' })} className="w-full" disabled={readonly}>
                      {triple.target_type}
                    </SelectTrigger>
                    <SelectContent>
                      {draft.schema.entity_types.map(item => (
                        <SelectItem key={item.id} value={item.name}>
                          <SelectItemText>{item.name}</SelectItemText>
                          <SelectItemIndicator />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    size="small"
                    tone="destructive"
                    variant="ghost"
                    disabled={readonly}
                    onClick={() => updateSchema(schema => ({
                      ...schema,
                      allowed_triples: schema.allowed_triples.filter((_, itemIndex) => itemIndex !== index),
                    }))}
                  >
                    {t('operation.delete', { ns: 'common' })}
                  </Button>
                </div>
              ))}
            </div>
          </div>
          {config?.graph_version && (
            <div className="body-xs-regular text-text-tertiary">
              {t('nodes.knowledgeBase.graphIndex.graphVersion', { ns: 'workflow' })}
              {': '}
              <code>{config.graph_version}</code>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const GraphIndexConfig = (props: GraphIndexConfigProps) => {
  return <GraphIndexConfigContent key={getConfigKey(props.config)} {...props} />
}

export default GraphIndexConfig
