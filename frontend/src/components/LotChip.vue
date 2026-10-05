<template>
  <span class="lot" :class="lot.alert_level">
    {{ lot.name }} ×{{ lot.qty_remain }} · {{ lot.expiry || '无到期' }}
    <em v-if="lot.opened" class="tag opened">已开封·剩{{ openedLeft }}</em>
    <em v-if="lot.alert_level === 'expired'" class="tag expired">{{ reasonText }}</em>
    <em v-else-if="lot.alert_level === 'soon'" class="tag soon">{{ soonText }}</em>
    <button v-if="openable && !asking" class="open-btn" @click="preview">开封</button>
    <span v-if="asking" class="open-confirm">
      钉住剩余 {{ hours }} 小时？
      <button @click="confirmOpen">确认</button>
      <button class="ghost" @click="asking = false">取消</button>
    </span>
    <span v-if="err" class="err">{{ err }}</span>
  </span>
</template>
<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
const props = defineProps({ lot: Object })
const emit = defineEmits(['changed'])
const asking = ref(false)
const hours = ref(null)
const err = ref('')
const openable = computed(() => !props.lot.opened && props.lot.qty_remain > 0)
const openedLeft = computed(() => {
  const h = props.lot.opened_remaining_hours
  return h == null ? '' : Math.max(0, h).toFixed(1) + 'h'
})
const reasonText = computed(() => (props.lot.alert_reason === 'opened' ? '开封超时' : '日历到期'))
const soonText = computed(() =>
  props.lot.alert_reason === 'opened' ? `开封剩${openedLeft.value}` : `剩${props.lot.days_left}天`)
async function preview() {
  err.value = ''
  try {
    const p = await api(`/lots/${props.lot.id}/open-preview`)
    hours.value = p.hours
    asking.value = true
  } catch (e) { err.value = e.message }
}
async function confirmOpen() {
  err.value = ''
  try {
    await api(`/lots/${props.lot.id}/open`, { method: 'POST', body: '{}' })
    asking.value = false
    window.dispatchEvent(new Event('refresh-alerts'))
    emit('changed')
  } catch (e) { err.value = e.message; asking.value = false }
}
</script>
