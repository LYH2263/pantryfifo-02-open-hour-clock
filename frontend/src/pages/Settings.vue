<template>
  <div>
    <h1>设置</h1>
    <label class="field">临期预警天数（warn_days）
      <input type="number" min="0" v-model.number="warn_days" />
    </label>
    <label class="field">默认开封小时（open_hours_default）
      <input type="number" min="1" step="0.5" v-model.number="open_hours_default" />
    </label>
    <button @click="save">保存</button>
    <p class="muted">已开封批的小时已钉在批上：改默认值只影响之后开封的批，已在架批的判定不变。</p>
    <pre>{{ msg }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const warn_days = ref(3)
const open_hours_default = ref(48)
const msg = ref('')
onMounted(async () => {
  const s = await api('/settings')
  warn_days.value = Number(s.warn_days)
  open_hours_default.value = Number(s.open_hours_default)
})
async function save() {
  try {
    const s = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({ warn_days: warn_days.value, open_hours_default: open_hours_default.value }),
    })
    msg.value = '已保存：' + JSON.stringify(s)
    window.dispatchEvent(new Event('refresh-alerts'))
  } catch (e) { msg.value = e.message }
}
</script>
