<!-- 数据表：列定义 columns[{key,title,width?,align?,formatter?(row)->string,slot?}]；
     加载/错误/空态内建；游标分页 hasMore+@load-more；行点击 @row-click(row)。
用法：<DataTable :columns="cols" :rows="rows" :loading="l" :error="e" @retry="load"
  :has-more="cur!=null" @load-more="loadMore" @row-click="openDetail"/> -->
<template>
  <div class="dt-wrap">
    <LoadingBlock v-if="loading" />
    <ErrorState v-else-if="error" :message="error" @retry="$emit('retry')" />
    <EmptyState v-else-if="!rows.length" :text="emptyText" />
    <template v-else>
      <div class="dt-scroll">
        <table class="dt-table">
          <thead>
            <tr>
              <th v-for="c in columns" :key="c.key" :style="{ width: c.width ? c.width : undefined, textAlign: c.align || 'left' }">{{ c.title }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in rows" :key="rowKey ? row[rowKey] : i"
                @click="$emit('row-click', row)" :class="{ clickable: !!rowClickable }">
              <td v-for="c in columns" :key="c.key" :style="{ textAlign: c.align || 'left' }">
                <slot v-if="c.slot" :name="c.slot" :row="row" :value="row[c.key]" />
                <template v-else>{{ c.formatter ? c.formatter(row) : (row[c.key] ?? '—') }}</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="hasMore" class="dt-more">
        <button class="ghost" :disabled="loadingMore" @click="$emit('load-more')">
          {{ loadingMore ? '加载中…' : '加载更多' }}</button>
      </div>
    </template>
  </div>
</template>
<script setup lang="ts">
import LoadingBlock from './LoadingBlock.vue'
import ErrorState from './ErrorState.vue'
import EmptyState from './EmptyState.vue'

export interface DtColumn {
  key: string
  title: string
  width?: string
  align?: 'left' | 'right' | 'center'
  formatter?: (row: any) => string
  slot?: string
}
withDefaults(defineProps<{
  columns: DtColumn[], rows: any[], loading?: boolean, loadingMore?: boolean,
  error?: string, emptyText?: string, rowKey?: string, hasMore?: boolean,
  rowClickable?: boolean
}>(), { emptyText: '暂无数据', hasMore: false, rowClickable: true })
defineEmits<{ (e: 'retry'): void, (e: 'load-more'): void,
  (e: 'row-click', row: any): void }>()
</script>
<style scoped>
.dt-wrap { min-height: 80px; }
.dt-scroll { overflow-x: auto; border-radius: var(--radius-lg); border: 1px solid var(--c-border); }
.dt-table { width: 100%; border-collapse: collapse; background: var(--c-surface); font-size: 13px; border-radius: 0; }
.dt-table thead th { position: sticky; top: 0; background: #eef2f7; z-index: 1; }
.dt-table td { border-top: 1px solid #edf0f4; }
.dt-table tbody tr:first-child td { border-top: 0; }
.dt-table tbody tr.clickable:hover { background: #f3f7fd; cursor: pointer; }
.dt-more { margin-top: 10px; text-align: center; }
</style>
