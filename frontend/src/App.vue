<template>
  <div>
    <div class="alert-bar" v-if="alerts.length">临期预警：{{ alerts.map(fmt).join(' · ') }}</div>
    <div class="alert-bar" v-else>临期预警带：暂无紧急批次</div>
    <div class="wrap">
      <nav class="layer-tabs">
        <router-link to="/">全层</router-link>
        <router-link to="/layer/upper">上层</router-link>
        <router-link to="/layer/mid">中层</router-link>
        <router-link to="/layer/lower">下层</router-link>
        <router-link to="/inbound">入库</router-link>
        <router-link to="/consume">消费</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
      <router-view />
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { api } from './api'
const alerts = ref([])
function fmt(a) {
  if (a.alert_level === 'expired') return `${a.name}·${a.alert_reason === 'opened' ? '开封超时' : '日历到期'}`
  if (a.alert_reason === 'opened') return `${a.name}·开封剩${Math.max(0, a.opened_remaining_hours).toFixed(1)}h`
  return `${a.name}·剩${a.days_left}天`
}
async function load() { try { alerts.value = await api('/alerts') } catch { alerts.value = [] } }
const router = useRouter()
router.afterEach(load)
onMounted(() => { load(); window.addEventListener('refresh-alerts', load) })
onBeforeUnmount(() => window.removeEventListener('refresh-alerts', load))
</script>
