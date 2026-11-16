<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const packs = ref([])
const releases = ref([])
const packId = ref(null)
const reason = ref('')
const last = ref(null)
const err = ref('')
const busy = ref(false)

async function refresh() {
  packs.value = await getJSON('/api/packs')
  releases.value = await getJSON('/api/releases')
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

async function decide(decision) {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/releases/decide', {
      pack_id: Number(packId.value),
      decision,
      reason: reason.value || null,
    })
    reason.value = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function decisionLabel(d) {
  return d === 'pass' ? '通过' : d === 'reject' ? '驳回' : d
}

function statusLabel(s) {
  return ({
    built: '已生成',
    checked_ok: '校验通过',
    checked_fail: '校验未过',
    released: '已放行',
    rejected: '已驳回',
    reference: '只读对照',
    voided: '已作废',
  })[s] || s
}
</script>
<template>
  <div class="page">
    <h1>放行台</h1>
    <p class="muted">
      仅清单全部通过才可放行；驳回须填写原因并留痕。只读对照包与已作废包不得放行。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <h3>做出决策</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ statusLabel(p.status) }}{{ p.releasable === false ? ' · 不可放行' : '' }}）
            </option>
          </select>
        </div>
        <div class="field" style="flex:1;min-width:14rem">
          <label>原因（驳回必填）</label>
          <input v-model="reason" style="width:100%" placeholder="例如：键重复，暂缓放行" />
        </div>
        <button :disabled="busy || !packId" @click="decide('pass')">放行通过</button>
        <button class="danger" :disabled="busy || !packId" @click="decide('reject')">驳回</button>
      </div>
      <p v-if="last">
        决策
        <span class="tag" :class="last.decision === 'pass' ? 'ok' : 'danger'">
          {{ decisionLabel(last.decision) }}
        </span>
        · {{ last.reason }}
      </p>
    </div>
    <div class="panel">
      <h3>决策留痕</h3>
      <table>
        <thead>
          <tr>
            <th>包号</th>
            <th>决策</th>
            <th>原因</th>
            <th>包状态</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td class="mono">{{ r.pack_no }}</td>
            <td>
              <span class="tag" :class="r.decision === 'pass' ? 'ok' : 'danger'">
                {{ decisionLabel(r.decision) }}
              </span>
            </td>
            <td>{{ r.reason }}</td>
            <td><span class="tag">{{ statusLabel(r.pack_status) }}</span></td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
