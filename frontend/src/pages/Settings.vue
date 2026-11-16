<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const LABEL = {
  default_sample_rate: '默认采样率',
  require_checklist_pass: '放行须清单全过',
  seed_policy: '种子策略',
  batch_policy: '换代策略',
}

const READONLY = new Set(['default_sample_rate', 'require_checklist_pass'])

const SEED_POLICY_OPTIONS = [
  {
    value: 'strict',
    title: '强制固定种子',
    desc: '必须显式填写种子；缺种子直接拒绝开跑，杜绝任何未入参随机源。',
  },
  {
    value: 'allow_default',
    title: '允许缺省种子',
    desc: '缺种子时按对象编号与算法版本确定性派生，并把实际采用的种子写回批次，仍可复现。',
  },
]

const BATCH_POLICY_OPTIONS = [
  {
    value: 'retain',
    title: '旧批次只读对照',
    desc: '改种子或采样率后，旧批次与未放行旧包降为只读对照，新采样另开批次；旧包不得沿用新样本。',
  },
  {
    value: 'void',
    title: '作废旧包并重打包',
    desc: '作废关联的未放行旧包及其清单，必须按新种子重打包后方可继续放行。',
  },
]

function displayValue(k, v) {
  if (k === 'require_checklist_pass') {
    return v === '1' || v === 1 || v === true || v === 'true' ? '是' : '否'
  }
  if (k === 'seed_policy') {
    return ({ strict: '强制固定种子', allow_default: '允许缺省种子' })[v] || v
  }
  if (k === 'batch_policy') {
    return ({ retain: '旧批次只读对照', void: '作废未放行旧包' })[v] || v
  }
  return v
}

const settings = ref({})
const seedPolicy = ref('strict')
const batchPolicy = ref('retain')
const err = ref('')
const saved = ref('')
const busy = ref(false)

async function load() {
  settings.value = await getJSON('/api/settings')
  seedPolicy.value = settings.value.seed_policy || 'strict'
  batchPolicy.value = settings.value.batch_policy || 'retain'
}

onMounted(async () => {
  try { await load() } catch (e) { err.value = String(e.message || e) }
})

async function save() {
  err.value = ''
  saved.value = ''
  busy.value = true
  try {
    await postJSON('/api/settings', {
      seed_policy: seedPolicy.value,
      batch_policy: batchPolicy.value,
    })
    await load()
    saved.value = '策略已保存，后续采样按新策略执行。'
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">采样策略互斥切换；策略变更会留痕。影子试跑与规则版本留给后题。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="saved" class="tag ok">{{ saved }}</p>

    <div class="panel">
      <h3>种子策略（互斥）</h3>
      <label v-for="opt in SEED_POLICY_OPTIONS" :key="opt.value" class="opt">
        <input type="radio" name="seed_policy" :value="opt.value" v-model="seedPolicy" />
        <span>
          <strong>{{ opt.title }}</strong>
          <span class="muted"> · {{ opt.desc }}</span>
        </span>
      </label>
    </div>

    <div class="panel">
      <h3>换代策略（互斥）</h3>
      <label v-for="opt in BATCH_POLICY_OPTIONS" :key="opt.value" class="opt">
        <input type="radio" name="batch_policy" :value="opt.value" v-model="batchPolicy" />
        <span>
          <strong>{{ opt.title }}</strong>
          <span class="muted"> · {{ opt.desc }}</span>
        </span>
      </label>
      <p class="muted">两种策略都只处置未放行证据；已放行证据不受影响，但不会被新种子样本套用。</p>
      <button :disabled="busy" @click="save">保存策略</button>
    </div>

    <div class="panel">
      <h3>当前配置</h3>
      <table>
        <thead>
          <tr>
            <th>配置项</th>
            <th>取值</th>
            <th>可改写</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k">
            <td>{{ LABEL[k] || k }}</td>
            <td><span class="tag">{{ displayValue(k, v) }}</span></td>
            <td>{{ READONLY.has(k) ? '只读' : '可改写' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<style scoped>
.opt {
  display: flex;
  gap: 0.55rem;
  align-items: flex-start;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
}
.opt:last-of-type { border-bottom: 0; margin-bottom: 0.6rem; }
.opt input { margin-top: 0.25rem; }
</style>
