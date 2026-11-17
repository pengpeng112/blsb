<!-- LT-02 改造：StatCard 网格 + Donut(submissions_by_state) + BarChart(quality_open_by_rule)。
     API 与参数不变：GET /api/v1/admin/overview。 -->
<template>
  <div>
    <PageHeader title="平台总览" subtitle="采集 / 质量 / 提交 / 发布一屏总览" mock />
    <LoadingBlock v-if="loading" />
    <ErrorState v-else-if="error" :message="error" @retry="load" />
    <template v-else-if="d">
      <div class="stat-grid">
        <StatCard label="采集事件" :value="d.acquisitions" />
        <StatCard label="可用文件" :value="d.files_available" />
        <StatCard label="已归集字节" :value="fmtB(d.bytes_available)" />
        <StatCard label="病理病例" :value="d.cases" />
        <StatCard label="临床就绪" :value="d.cases_clinically_ready" tone="success" />
        <StatCard label="待复核问题" :value="d.quality_open" tone="warning" />
        <StatCard label="提交（Mock）" :value="d.submissions" />
        <StatCard label="视图发布" :value="d.publications" />
        <StatCard label="设备 在线/总数" :value="`${d.agents_online}/${d.agents_total}`"
                  :tone="d.agents_online > 0 ? 'success' : 'danger'" />
        <StatCard label="最老待处理(秒)" :value="Math.round(d.oldest_pending_job_age_seconds)"
                  :tone="d.oldest_pending_job_age_seconds > 300 ? 'warning' : 'info'" />
      </div>
      <div class="ov-charts">
        <div class="card">
          <b>提交状态分布</b>
          <p class="muted">ACCEPTED/CONFIRMED 需回执证据；无回执不显示医保成功</p>
          <Donut :parts="subParts" caption="提交" />
        </div>
        <div class="card">
          <b>质量问题分布（未决）</b>
          <BarChart :cats="qualityCats" :stacked="false" :height="200" />
        </div>
      </div>
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'
import Donut from '../components/charts/Donut.vue'
import BarChart from '../components/charts/BarChart.vue'
import LoadingBlock from '../components/LoadingBlock.vue'
import ErrorState from '../components/ErrorState.vue'
import { formatBytes } from '../utils/bytes'

const d = ref<any>(null), loading = ref(true), error = ref('')
const BUCKET_COLORS: Record<string, string> = {
  PREPARED: '#0e7490', SENDING: '#0891b2', ACCEPTED: '#b45309',
  CONFIRMED: '#1a7f37', PARTIAL: '#d97706', REJECTED: '#c62828',
  UNKNOWN: '#9c2b2b', SUPERSEDED: '#8c959f',
}
const subParts = computed(() =>
  Object.entries(d.value?.submissions_by_state ?? {})
    .map(([k, v]) => ({ name: k, color: BUCKET_COLORS[k] ?? '#8c959f', value: Number(v) })))
const qualityCats = computed(() =>
  Object.entries(d.value?.quality_open_by_rule ?? {})
    .map(([k, v]) => ({ label: k, values: [{ name: 'OPEN', color: '#b45309', value: Number(v) }] })))
function fmtB(n: number) { return formatBytes(n) }
async function load() {
  loading.value = true; error.value = ''
  try { d.value = await api.coreGet('/api/v1/admin/overview') }
  catch (x: any) { error.value = x.message || '加载失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>
<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px; margin-bottom: 16px; }
.ov-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 1000px) { .ov-charts { grid-template-columns: 1fr; } }
</style>
