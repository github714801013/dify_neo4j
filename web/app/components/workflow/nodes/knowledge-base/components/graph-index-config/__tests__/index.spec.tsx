import type { ReactNode } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import GraphIndexConfig from '..'

const mockUseModelList = vi.hoisted(() => vi.fn())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/app/components/header/account-setting/model-provider-page/hooks', () => ({
  useModelList: mockUseModelList,
}))

vi.mock('@/app/components/header/account-setting/model-provider-page/model-selector', () => ({
  default: ({ onSelect, readonly }: { onSelect: (model: { provider: string, model: string, plugin_id: string }) => void, readonly?: boolean }) => (
    <button
      disabled={readonly}
      onClick={() => onSelect({ provider: 'openai', model: 'gpt-4.1-mini', plugin_id: 'langgenius/openai' })}
    >
      select-model
    </button>
  ),
}))

vi.mock('@langgenius/dify-ui/button', () => ({
  Button: ({ children, size: _size, tone: _tone, variant: _variant, ...props }: { children: ReactNode, size?: string, tone?: string, variant?: string }) => (
    <button {...props}>{children}</button>
  ),
}))

vi.mock('@langgenius/dify-ui/input', () => ({
  Input: ({ ...props }) => <input {...props} />,
}))

vi.mock('@langgenius/dify-ui/textarea', () => ({
  Textarea: ({ onValueChange, ...props }: { onValueChange: (value: string) => void }) => <textarea {...props} onChange={event => onValueChange(event.target.value)} />,
}))

vi.mock('@langgenius/dify-ui/switch', () => ({
  Switch: ({ checked, onCheckedChange, ...props }: { checked: boolean, onCheckedChange: (checked: boolean) => void }) => (
    <input
      {...props}
      type="checkbox"
      checked={checked}
      onChange={event => onCheckedChange(event.target.checked)}
    />
  ),
}))

vi.mock('@/app/components/base/param-item', () => ({
  default: ({ id, name, value, onChange, disabled }: { id: string, name: string, value: number, onChange: (id: string, value: number) => void, disabled?: boolean }) => (
    <button disabled={disabled} aria-label={name} onClick={() => onChange(id, value + 0.1)}>
      {name}
    </button>
  ),
}))

vi.mock('@langgenius/dify-ui/select', () => ({
  Select: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItemIndicator: () => null,
  SelectItemText: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  SelectTrigger: ({ children, ...props }: { children: ReactNode }) => <button {...props}>{children}</button>,
}))

const emptyTemplateConfig = {
  enabled: false,
  schema: {
    entity_types: [],
    relation_types: [],
    allowed_triples: [],
  },
}

const completeConfig = {
  enabled: true,
  schema: {
    entity_types: [{ name: 'PRODUCT', properties: [] as [] }],
    relation_types: [{ name: 'CONTAINS', properties: [] as [] }],
    allowed_triples: [{
      source_type: 'PRODUCT',
      relation_type: 'CONTAINS',
      target_type: 'PRODUCT',
    }],
  },
  extract_model_config: {
    provider: 'openai',
    model: 'gpt-4.1-mini',
    temperature: 0,
    max_tokens: undefined,
    max_triplets_per_chunk: 10,
    strict: true,
  },
}

describe('GraphIndexConfig', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseModelList.mockReturnValue({ data: [] })
  })

  it('initializes JSON editor with the document schema by default', () => {
    render(<GraphIndexConfig onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('checkbox'))

    const json = screen.getByRole('textbox', { name: 'nodes.knowledgeBase.graphIndex.jsonSchema' }) as HTMLTextAreaElement
    expect(JSON.parse(json.value).entity_types.map((item: { name: string }) => item.name)).toEqual([
      'product',
      'module',
      'feature',
      'version',
      'api',
      'parameter',
      'error',
      'document',
    ])
    expect(JSON.parse(json.value).relation_types.map((item: { name: string }) => item.name)).toEqual([
      'contains',
      'supports',
      'depends_on',
      'available_in',
      'configures',
      'calls',
      'returns',
      'causes',
      'solves',
      'describes',
    ])
  })

  it('imports a complete schema from JSON without accepting invalid JSON', () => {
    const onChange = vi.fn()
    render(<GraphIndexConfig config={completeConfig} onChange={onChange} />)

    const json = screen.getByRole('textbox', { name: 'nodes.knowledgeBase.graphIndex.jsonSchema' }) as HTMLTextAreaElement
    fireEvent.change(json, {
      target: {
        value: JSON.stringify({
          entity_types: [{ name: 'document', properties: [] }],
          relation_types: [{ name: 'describes', properties: [] }],
          allowed_triples: [{ source_type: 'document', relation_type: 'describes', target_type: 'document' }],
        }),
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.importJson' }))

    expect(onChange).toHaveBeenLastCalledWith({
      ...completeConfig,
      schema: {
        entity_types: [{ name: 'document', properties: [] }],
        relation_types: [{ name: 'describes', properties: [] }],
        allowed_triples: [{ source_type: 'document', relation_type: 'describes', target_type: 'document' }],
      },
    })

    fireEvent.change(json, { target: { value: '{invalid' } })
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.importJson' }))
    expect(screen.getByRole('alert')).toHaveTextContent('nodes.knowledgeBase.graphIndex.invalidJson')
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('does not persist an incomplete enabled configuration', () => {
    const onChange = vi.fn()

    render(<GraphIndexConfig onChange={onChange} />)

    fireEvent.click(screen.getByRole('checkbox'))

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('nodes.knowledgeBase.graphIndex.incomplete')
  })

  it('initializes a newly enabled node from the dataset extraction template', () => {
    const onChange = vi.fn()

    render(<GraphIndexConfig templateConfig={completeConfig} onChange={onChange} />)

    fireEvent.click(screen.getByRole('checkbox'))

    expect(onChange).toHaveBeenLastCalledWith(completeConfig)
  })

  it('uses lowercase names for newly added entity and relation types', () => {
    render(<GraphIndexConfig templateConfig={emptyTemplateConfig} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addEntityType' }))
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addRelationType' }))

    expect(screen.getByDisplayValue('entity')).toBeInTheDocument()
    expect(screen.getByDisplayValue('relation')).toBeInTheDocument()
  })

  it('persists only the complete graph index contract without model credentials or runtime retrieval options', () => {
    const onChange = vi.fn()

    render(<GraphIndexConfig templateConfig={emptyTemplateConfig} onChange={onChange} />)

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addEntityType' }))
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addRelationType' }))
    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addAllowedTriple' }))
    fireEvent.click(screen.getByRole('button', { name: 'select-model' }))

    expect(onChange).toHaveBeenLastCalledWith({
      enabled: true,
      schema: {
        entity_types: [{ name: 'entity', properties: [] }],
        relation_types: [{ name: 'relation', properties: [] }],
        allowed_triples: [{
          source_type: 'entity',
          relation_type: 'relation',
          target_type: 'entity',
        }],
      },
      extract_model_config: {
        provider: 'openai',
        model: 'gpt-4.1-mini',
        temperature: 0,
        max_tokens: undefined,
        max_triplets_per_chunk: 10,
        strict: true,
      },
    })
    expect(onChange.mock.calls.flat()).not.toContain('langgenius/openai')
  })

  it('removes triples that reference a deleted entity type', () => {
    render(<GraphIndexConfig config={completeConfig} onChange={vi.fn()} />)

    expect(screen.getByTestId('graph-index-triple-0')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: 'operation.delete' })[0]!)

    expect(screen.queryByTestId('graph-index-triple-0')).not.toBeInTheDocument()
  })

  it('persists configured entity properties when they are edited', () => {
    const onChange = vi.fn()
    const config = {
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        entity_types: [{
          name: 'PRODUCT',
          properties: [{
            name: 'release_date',
            description: 'The date the product was released.',
            value_type: 'date' as const,
            required: false,
          }],
        }],
      },
    }

    render(<GraphIndexConfig config={config} onChange={onChange} />)

    expect(screen.getByDisplayValue('release_date')).toBeInTheDocument()
    expect(screen.getByDisplayValue('The date the product was released.')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyDescription'), {
      target: { value: 'The launch date of the product.' },
    })

    expect(onChange).toHaveBeenLastCalledWith({
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        entity_types: [{
          name: 'PRODUCT',
          properties: [{
            name: 'release_date',
            description: 'The launch date of the product.',
            value_type: 'date',
            required: false,
          }],
        }],
      },
    })
  })

  it('persists configured relation properties when they are edited', () => {
    const onChange = vi.fn()
    const config = {
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        relation_types: [{
          name: 'CONTAINS',
          properties: [{
            name: 'source',
            description: 'Where the relation was extracted from.',
            value_type: 'string' as const,
            required: false,
          }],
        }],
      },
    }

    render(<GraphIndexConfig config={config} onChange={onChange} />)

    expect(screen.getByDisplayValue('source')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Where the relation was extracted from.')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyDescription'), {
      target: { value: 'The document source of this relation.' },
    })

    expect(onChange).toHaveBeenLastCalledWith({
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        relation_types: [{
          name: 'CONTAINS',
          properties: [{
            name: 'source',
            description: 'The document source of this relation.',
            value_type: 'string',
            required: false,
          }],
        }],
      },
    })
  })

  it('persists configured extraction parameters when they are changed', () => {
    const onChange = vi.fn()
    const config = {
      ...completeConfig,
      extract_model_config: {
        ...completeConfig.extract_model_config,
        temperature: 0.3,
        max_tokens: 2048,
        max_triplets_per_chunk: 12,
        strict: false,
      },
    }

    render(<GraphIndexConfig config={config} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'form.graphRag.temperature' }))

    expect(onChange).toHaveBeenLastCalledWith({
      ...config,
      extract_model_config: {
        ...config.extract_model_config,
        temperature: 0.4,
      },
    })
  })

  it('adds an entity property with its scalar type and required flag', () => {
    const onChange = vi.fn()

    render(<GraphIndexConfig config={completeConfig} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addEntityProperty' }))
    fireEvent.change(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyName'), {
      target: { value: 'product_code' },
    })
    fireEvent.change(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyDescription'), {
      target: { value: 'The catalog code.' },
    })
    fireEvent.change(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyValueType'), {
      target: { value: 'number' },
    })
    fireEvent.click(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyRequired'))

    expect(onChange).toHaveBeenLastCalledWith({
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        entity_types: [{
          name: 'PRODUCT',
          properties: [{
            name: 'product_code',
            description: 'The catalog code.',
            value_type: 'number',
            required: true,
          }],
        }],
      },
    })
  })

  it('does not persist an entity property until its name, description, and uniqueness are valid', () => {
    const onChange = vi.fn()
    const config = {
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        entity_types: [{
          name: 'PRODUCT',
          properties: [{
            name: 'property',
            description: 'An existing property.',
            value_type: 'string' as const,
            required: false,
          }],
        }],
      },
    }

    render(<GraphIndexConfig config={config} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addEntityProperty' }))
    fireEvent.change(screen.getAllByLabelText('nodes.knowledgeBase.graphIndex.propertyDescription')[1]!, {
      target: { value: 'A second property.' },
    })
    fireEvent.change(screen.getAllByLabelText('nodes.knowledgeBase.graphIndex.propertyName')[1]!, {
      target: { value: 'invalid-name' },
    })

    expect(onChange).not.toHaveBeenCalled()

    fireEvent.change(screen.getAllByLabelText('nodes.knowledgeBase.graphIndex.propertyName')[1]!, {
      target: { value: 'second_property' },
    })

    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('removes an entity property from the persisted configuration', () => {
    const onChange = vi.fn()
    const config = {
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        entity_types: [{
          name: 'PRODUCT',
          properties: [{
            name: 'release_date',
            description: 'The date the product was released.',
            value_type: 'date' as const,
            required: false,
          }],
        }],
      },
    }

    render(<GraphIndexConfig config={config} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.deleteEntityProperty' }))

    expect(screen.queryByDisplayValue('release_date')).not.toBeInTheDocument()
    expect(onChange).toHaveBeenLastCalledWith({
      ...completeConfig,
      schema: {
        ...completeConfig.schema,
        entity_types: [{ name: 'PRODUCT', properties: [] }],
      },
    })
  })

  it('shows graph version as read-only text and disables every editor control in read-only mode', () => {
    const config = {
      ...completeConfig,
      graph_version: 'version-1',
      schema: {
        ...completeConfig.schema,
        entity_types: [{
          name: 'PRODUCT',
          properties: [{
            name: 'release_date',
            description: 'The date the product was released.',
            value_type: 'date' as const,
            required: false,
          }],
        }],
      },
    }

    render(<GraphIndexConfig config={config} onChange={vi.fn()} readonly />)

    expect(screen.getByText('version-1')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('version-1')).not.toBeInTheDocument()
    expect(screen.getByLabelText('nodes.knowledgeBase.graphIndex.title')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'select-model' })).toBeDisabled()
    expect(screen.getByDisplayValue('PRODUCT')).toBeDisabled()
    expect(screen.getByDisplayValue('CONTAINS')).toBeDisabled()
    expect(screen.getByDisplayValue('release_date')).toBeDisabled()
    expect(screen.getByDisplayValue('The date the product was released.')).toBeDisabled()
    expect(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyValueType')).toBeDisabled()
    expect(screen.getByLabelText('nodes.knowledgeBase.graphIndex.propertyRequired')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.addEntityProperty' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'nodes.knowledgeBase.graphIndex.deleteEntityProperty' })).toBeDisabled()
    expect(screen.getAllByRole('button').every(button => (button as HTMLButtonElement).disabled)).toBe(true)
  })
})
