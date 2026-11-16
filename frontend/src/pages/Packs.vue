<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const batches = ref([])
const packs = ref([])
const batchId = ref(null)
const last = ref(null)
const err = ref('')
const busy = ref(false)

const STATUS = {
  built: '已生成',
  checked_ok: '校验通过',
  checked_fail: '校验未过',
  released: '已放行',
  rejected: '已驳回',
  reference: '只读对照',
  voided: '已作废',
}

const openBatches = computed(() => batches.value.filter((b) => b.status === 'ready'))

async function refresh() {
  ;[batches.value, packs.value] = await Promise.all([
    getJSON('/api/samples'),
    getJSON('/api/packs'),
  ])
  if (!batchId.value) {
    const first = openBatches.value[0] || batches.value[0]
    if (first) batchId.value = first.id
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

async function build() {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/packs/build', { batch_id: Number(batchId.value) })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="page">
    <h1>证据包</h1>
    <p class="muted">
      由采样批次生成证据包：条目摘要、条数与包指纹；并写明来源批次的种子、采样率与样本数。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <h3>生成证据包</h3>
      <div class="row">
        <div class="field">
          <label>采样批次（仅就绪批次可打包）</label>
          <select v-model="batchId">
            <option v-for="b in batches" :key="b.id" :value="b.id" :disabled="b.status !== 'ready'">
              {{ b.batch_no }}（{{ b.subject_title }} · {{ b.sample_count }} 条 · {{ b.status === 'ready' ? b.seed : (STATUS[b.status] || b.status) }}）
            </option>
          </select>
        </div>
        <button :disabled="busy || !batchId" @click="build">生成证据包</button>
      </div>
      <div v-if="last" style="margin-top:0.75rem">
        <p>
          包号 <span class="mono">{{ last.pack_no }}</span>
          · 条目 <strong>{{ last.item_count }}</strong>
          · 来源批次 <span class="mono">{{ last.batch_no }}</span>
        </p>
        <p class="mono muted">
          种子 {{ last.seed }} · 采样率 {{ last.rate }} · 源 {{ last.source_count }} / 样本 {{ last.sample_count }}
        </p>
        <p class="mono muted">指纹 {{ last.fingerprint }}</p>
      </div>
    </div>
    <div class="panel">
      <h3>证据包列表</h3>
      <table>
        <thead>
          <tr>
            <th>包号</th>
            <th>对象</th>
            <th>来源批次</th>
            <th>种子</th>
            <th>条目数</th>
            <th>指纹</th>
            <th>状态</th>
            <th>可放行</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in packs" :key="p.id">
            <td class="mono">{{ p.pack_no }}</td>
            <td>{{ p.subject_title }}</td>
            <td class="mono">{{ p.batch_no || p.batch_id }}</td>
            <td class="mono">
              {{ p.seed }}
              <span v-if="p.batch_seed_recorded && p.batch_seed_recorded !== p.seed" class="muted">
                （批次 {{ p.batch_seed_recorded }}）
              </span>
            </td>
            <td>{{ p.item_count }}</td>
            <td class="mono" style="max-width:10rem;overflow:hidden;text-overflow:ellipsis">{{ p.fingerprint }}</td>
            <td>
              <span class="tag" :class="['reference', 'voided'].includes(p.status) ? 'warn' : ''">
                {{ STATUS[p.status] || p.status }}
              </span>
            </td>
            <td>
              <span class="tag" :class="p.releasable ? 'ok' : 'danger'">{{ p.releasable ? '可放行' : '不可放行' }}</span>
            </td>
            <td class="mono">{{ p.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
