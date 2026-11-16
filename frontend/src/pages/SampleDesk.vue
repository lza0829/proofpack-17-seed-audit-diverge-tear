<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const subjects = ref([])
const batches = ref([])
const settings = ref({})
const subjectId = ref(null)
const rate = ref(0.3)
const seed = ref('')
const last = ref(null)
const verify = ref(null)
const err = ref('')
const busy = ref(false)

const seedPolicy = computed(() => settings.value.seed_policy || 'strict')
const batchPolicy = computed(() => settings.value.batch_policy || 'retain')
const seedRequired = computed(() => seedPolicy.value === 'strict')

async function refresh() {
  ;[subjects.value, batches.value, settings.value] = await Promise.all([
    getJSON('/api/subjects'),
    getJSON('/api/samples'),
    getJSON('/api/settings'),
  ])
  if (!subjectId.value && subjects.value.length) subjectId.value = subjects.value[0].id
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

async function run() {
  err.value = ''
  verify.value = null
  busy.value = true
  try {
    const body = { subject_id: Number(subjectId.value), rate: Number(rate.value) }
    if (seed.value.trim()) body.seed = seed.value.trim()
    last.value = await postJSON('/api/samples/run', body)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function check(batchId) {
  err.value = ''
  try {
    verify.value = await getJSON(`/api/samples/${batchId}/verify`)
  } catch (e) {
    err.value = String(e.message || e)
  }
}

const STATUS = {
  ready: '就绪',
  reference: '只读对照',
  voided: '已作废',
}
</script>
<template>
  <div class="page">
    <h1>采样台</h1>
    <p class="muted">
      按固定种子对举证对象做确定性抽样：同一对象、同一采样率、同一种子，重复采样得到的业务键集合与条数必须完全一致。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <h3>当前采样策略</h3>
      <p>
        种子策略
        <span class="tag" :class="seedRequired ? 'ok' : 'warn'">
          {{ seedRequired ? '强制固定种子（缺种子拒绝开跑）' : '允许缺省种子（派生种子写回批次）' }}
        </span>
        · 换代策略
        <span class="tag">{{ batchPolicy === 'retain' ? '旧批次只读对照，新采样另开批次' : '作废未放行旧包及清单，强制重打包' }}</span>
      </p>
      <p class="muted">可在<router-link to="/settings">设置</router-link>中切换。</p>
    </div>
    <div class="panel">
      <h3>执行采样</h3>
      <div class="row">
        <div class="field">
          <label>举证对象</label>
          <select v-model="subjectId">
            <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.title }}</option>
          </select>
        </div>
        <div class="field">
          <label>采样率</label>
          <input type="number" min="0" max="1" step="0.05" v-model.number="rate" />
        </div>
        <div class="field">
          <label>种子{{ seedRequired ? '（必填）' : '（可留空）' }}</label>
          <input
            v-model="seed"
            class="mono"
            style="min-width:16rem"
            :placeholder="seedRequired ? '请输入显式种子' : '留空则按对象确定性派生并写回批次'"
          />
        </div>
        <button :disabled="busy || !subjectId || (seedRequired && !seed.trim())" @click="run">执行采样</button>
      </div>
      <div v-if="last" style="margin-top:0.75rem">
        <p>
          批次 <span class="mono">{{ last.batch_no }}</span>
          · 源 <strong>{{ last.source_count }}</strong>
          · 样本 <strong>{{ last.sample_count }}</strong>
          · 采样率 {{ last.rate }}
          · 种子 <span class="tag">{{ last.seed }}</span>
          <span class="tag">{{ last.seed_source === 'derived' ? '派生' : '显式' }}</span>
        </p>
        <p class="mono muted">
          算法 {{ last.algorithm }} · 样本键指纹 {{ last.sample_keys_fingerprint }}
        </p>
        <p v-if="last.superseded && last.superseded.length">
          本次种子对齐：
          <span v-for="a in last.superseded" :key="`${a.batch_id}-${a.pack_id || 'batch'}`" class="tag warn" style="margin-right:0.4rem">
            {{ a.batch_no }}{{ a.pack_no ? ' / ' + a.pack_no : '' }} → {{ a.action }}
            <span v-if="a.aligned_seed" class="mono">（{{ a.previous_seed }} → {{ a.aligned_seed }}）</span>
          </span>
        </p>
      </div>
    </div>
    <div class="panel" v-if="verify">
      <h3>同种子重跑复核 · {{ verify.batch_no }}</h3>
      <p>
        <span class="tag" :class="verify.reproducible ? 'ok' : 'danger'">
          {{ verify.reproducible ? '可复现' : '不可复现' }}
        </span>
        · 记录样本 {{ verify.sample_count }} 条 · 重跑 {{ verify.replayed_sample_count }} 条
      </p>
      <p class="muted">
        种子 <span class="mono">{{ verify.seed }}</span>（{{ verify.seed_source }}）
        · 采样率 {{ verify.rate }} · 算法 {{ verify.algorithm }}
      </p>
      <p v-if="verify.reasons && verify.reasons.length" class="tag danger">{{ verify.reasons.join('；') }}</p>
      <p class="muted mono">记录键指纹 {{ verify.sample_keys_fingerprint_recorded }}</p>
      <p class="muted mono">重跑键指纹 {{ verify.sample_keys_fingerprint_replayed }}</p>
    </div>
    <div class="panel">
      <h3>采样批次</h3>
      <table>
        <thead>
          <tr>
            <th>批次号</th>
            <th>对象</th>
            <th>种子</th>
            <th>采样率</th>
            <th>源条数</th>
            <th>样本数</th>
            <th>状态</th>
            <th>时间</th>
            <th>复核</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.subject_title }}</td>
            <td class="mono">
              {{ b.seed }}
              <span class="tag" :class="b.seed_source === 'derived' ? 'warn' : ''">
                {{ b.seed_source === 'derived' ? '派生' : '显式' }}
              </span>
            </td>
            <td>{{ b.rate }}</td>
            <td>{{ b.source_count }}</td>
            <td>{{ b.sample_count }}</td>
            <td>
              <span class="tag" :class="b.status === 'ready' ? '' : 'warn'">{{ STATUS[b.status] || b.status }}</span>
            </td>
            <td class="mono">{{ b.created_at }}</td>
            <td><button class="ghost" @click="check(b.id)">重跑核对</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
