// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import RelationTargetPicker from './RelationTargetPicker.vue'
import type { RelationDraft } from '../api/characters'
it('emits counter and unresolved named target edits to its parent', async () => {
  const model = ref<RelationDraft>({
    type: 'rival_of',
    tier: 'entity',
    targetId: 'E1',
    targetKey: '',
    targetName: '',
    counter: '',
  })
  const Parent = defineComponent({
    components: { RelationTargetPicker },
    setup: () => ({ model }),
    template:
      '<RelationTargetPicker v-model="model" group-name="test" :entities="[]" :staged="[]" />',
  })
  const wrapper = mount(Parent)
  await wrapper.get('[aria-label="Relation counter"]').setValue('5')
  expect(model.value.counter).toBe('5')
  await wrapper.get('input[value="name"]').setValue()
  await wrapper.get('[aria-label="Named target"]').setValue('Missing person')
  expect(model.value.targetName).toBe('Missing person')
  expect(wrapper.text()).toContain('No committed entity matches')
  wrapper.unmount()
})
