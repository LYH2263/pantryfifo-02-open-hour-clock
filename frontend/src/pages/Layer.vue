<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <LotChip v-for="x in rows" :key="x.id" :lot="x" @changed="load" />
  </div>
</template>
<script setup>
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
import LotChip from '../components/LotChip.vue'
const props = defineProps({ layer: String })
const rows = ref([])
async function load() { rows.value = await api('/fridge?layer=' + props.layer) }
watch(() => props.layer, load)
onMounted(load)
</script>
