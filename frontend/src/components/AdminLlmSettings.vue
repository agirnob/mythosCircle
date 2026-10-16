<script setup lang="ts">
/* global Event, HTMLInputElement */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, apiFetch } from '../api/client'
import type { components } from '../api/schema'
import { useAuthStore } from '../stores/auth'

type Settings = components['schemas']['LLMSettingsView']
type Config = components['schemas']['LLMRoutingConfig']
type Provider = 'local' | 'gemini' | 'openrouter'
const providers = ['local', 'gemini', 'openrouter'] as const
const providerNames: Record<Provider, string> = {
  local: 'local',
  gemini: 'Gemini',
  openrouter: 'OpenRouter',
}
const emit = defineEmits<{ accessLost: [] }>()
const auth = useAuthStore()
const settings = ref<Settings | null>(null)
const busy = ref(false)
const testingProvider = ref<Provider | null>(null)
const error = ref('')
const feedback = ref('')
let generation = 0
let active = true
let retryAction: () => Promise<void> = load

function clear() {
  generation++
  settings.value = null
  busy.value = false
  testingProvider.value = null
  error.value = ''
  feedback.value = ''
}
function failure(err: unknown, version: number) {
  if (!active) return
  if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
    active = false
    clear()
    emit('accessLost')
    return
  }
  if (version !== generation) return
  error.value = err instanceof ApiError ? err.message : 'Could not reach the server. Please retry.'
}
async function load() {
  if (!active || !auth.isAdmin) return
  const version = ++generation
  busy.value = true
  error.value = ''
  feedback.value = ''
  retryAction = load
  try {
    const result = await apiFetch<Settings>('/api/admin/llm-settings')
    if (version === generation && active) settings.value = result
  } catch (err) {
    failure(err, version)
  } finally {
    if (version === generation) busy.value = false
  }
}
function config(): Config | null {
  if (!settings.value) return null
  const { primary, backup, local, gemini, openrouter } = settings.value
  return { primary, backup, local, gemini, openrouter }
}
async function save() {
  const body = config()
  if (!active || busy.value || !body) return
  const version = ++generation
  busy.value = true
  error.value = ''
  feedback.value = ''
  retryAction = save
  try {
    const result = await apiFetch<Settings>('/api/admin/llm-settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    })
    if (version !== generation || !active) return
    settings.value = result
    feedback.value = 'Saved. Jobs started from now on use this routing.'
  } catch (err) {
    failure(err, version)
    if (version === generation && error.value)
      error.value +=
        ' Save could not be confirmed. Retry the same settings or reload saved settings.'
  } finally {
    if (version === generation) busy.value = false
  }
}
async function test(provider: Provider) {
  if (!active || busy.value || !settings.value) return
  const version = ++generation
  testingProvider.value = provider
  busy.value = true
  error.value = ''
  feedback.value = ''
  retryAction = () => test(provider)
  try {
    const result = await apiFetch<components['schemas']['LLMTestResult']>(
      '/api/admin/llm-settings/test',
      {
        method: 'POST',
        body: JSON.stringify({ provider, profile: settings.value[provider] }),
      },
    )
    if (version !== generation || !active) return
    if (result.success) feedback.value = result.message
    else error.value = result.message
  } catch (err) {
    failure(err, version)
  } finally {
    if (version === generation) {
      busy.value = false
      testingProvider.value = null
    }
  }
}
function validatePositiveTimeout(event: Event) {
  const input = event.target as HTMLInputElement
  input.setCustomValidity(Number(input.value) > 0 ? '' : 'Enter a timeout greater than zero.')
}
watch(
  settings,
  () => {
    error.value = ''
    feedback.value = ''
  },
  { deep: true, flush: 'sync' },
)
function setOptionalNumber(
  provider: Provider,
  field: 'temperature' | 'top_p' | 'seed',
  event: Event,
) {
  if (!settings.value) return
  const value = (event.target as HTMLInputElement).value
  settings.value[provider][field] = value === '' ? null : Number(value)
}
watch(
  () => settings.value?.primary,
  (primary) => {
    if (settings.value && settings.value.backup === primary) settings.value.backup = null
  },
  { flush: 'sync' },
)
watch(
  [() => auth.account?.id, () => auth.isAdmin],
  () => {
    active = false
    clear()
  },
  { flush: 'sync' },
)
onMounted(() => {
  void load()
})
onBeforeUnmount(() => {
  active = false
  clear()
})
</script>

<template>
  <section class="llm-settings" aria-labelledby="llm-heading">
    <h2 id="llm-heading">Text generation providers</h2>
    <p>
      Choose a primary provider and an optional backup for connection failures, rate limits and
      server errors.
    </p>
    <p v-if="busy" role="status">
      {{
        testingProvider === 'gemini' || testingProvider === 'openrouter'
          ? `Testing ${providerNames[testingProvider]}… This may take up to two minutes.`
          : 'Working…'
      }}
    </p>
    <p v-if="error" role="alert">
      {{ error }}
      <button type="button" class="mc-btn mc-btn-secondary" :disabled="busy" @click="retryAction()">
        Retry
      </button>
    </p>
    <p v-if="feedback" role="status">{{ feedback }}</p>
    <form v-if="settings" @submit.prevent="save">
      <fieldset :disabled="busy">
        <legend>Routing</legend>
        <p>
          {{
            settings.saved
              ? 'Saved settings override deployment defaults.'
              : 'Using deployment defaults until you save.'
          }}
        </p>
        <div class="routing-fields">
          <label
            >Primary
            <select v-model="settings.primary" class="mc-input">
              <option value="local">Local OpenAI-compatible</option>
              <option value="gemini" :disabled="!settings.gemini_key_configured">
                Google Gemini
              </option>
              <option value="openrouter" :disabled="!settings.openrouter_key_configured">
                OpenRouter
              </option>
            </select>
          </label>
          <label
            >Backup
            <select v-model="settings.backup" class="mc-input">
              <option :value="null">None</option>
              <option value="local" :disabled="settings.primary === 'local'">
                Local OpenAI-compatible
              </option>
              <option
                value="gemini"
                :disabled="settings.primary === 'gemini' || !settings.gemini_key_configured"
              >
                Google Gemini
              </option>
              <option
                value="openrouter"
                :disabled="settings.primary === 'openrouter' || !settings.openrouter_key_configured"
              >
                OpenRouter
              </option>
            </select>
          </label>
        </div>
      </fieldset>
      <fieldset v-for="provider in providers" :key="provider" :disabled="busy">
        <legend>
          {{
            provider === 'local'
              ? 'Local OpenAI-compatible'
              : provider === 'gemini'
                ? 'Google Gemini'
                : 'OpenRouter'
          }}
        </legend>
        <p>
          {{
            provider === 'local'
              ? settings.local_key_configured
                ? 'Local key configured.'
                : 'No local key configured (optional).'
              : provider === 'gemini'
                ? settings.gemini_key_configured
                  ? 'Gemini key configured.'
                  : 'Gemini key missing.'
                : settings.openrouter_key_configured
                  ? 'OpenRouter key configured.'
                  : 'OpenRouter key missing.'
          }}
        </p>
        <p v-if="provider === 'openrouter' && !settings.openrouter_key_configured">
          Add MYTHOSCIRCLE_OPENROUTER_API_KEY to the API service environment in your Compose stack
          and set its value in Portainer stack variables. Recreate the API container, then reload
          this panel. For this installation, update /home/homest/mythos-redeploy.yml and the
          deployment runner environment as described in deploy/AUTO_DEPLOY.md. Never enter the key
          here.
        </p>
        <p v-if="provider === 'openrouter'">
          The default free router may choose different models. For reliable Forge output, choose a
          model that supports structured JSON and your output token limit. Check its price and
          limits on
          <a href="https://openrouter.ai/models" target="_blank" rel="noopener noreferrer"
            >OpenRouter</a
          >.
        </p>
        <p v-if="provider === 'gemini' && !settings.gemini_key_configured">
          Add MYTHOSCIRCLE_GEMINI_API_KEY to the API service environment in your Compose stack and
          set its value in Portainer stack variables. Recreate the API container, then reload this
          panel. For this installation, update /home/homest/mythos-redeploy.yml and the deployment
          runner environment as described in deploy/AUTO_DEPLOY.md. Never enter the key here.
        </p>
        <details>
          <summary>Advanced provider settings</summary>
          <div class="profile-fields">
            <label
              >Endpoint<input
                v-model="settings[provider].endpoint"
                class="mc-input"
                type="url"
                required
                :readonly="provider !== 'local'"
            /></label>
            <label
              >Model<input
                v-model="settings[provider].model"
                class="mc-input"
                required
                maxlength="200"
            /></label>
            <label
              >Timeout (seconds)<input
                v-model.number="settings[provider].timeout"
                class="mc-input"
                type="number"
                min="0"
                step="any"
                max="3600"
                required
                @input="validatePositiveTimeout"
            /></label>
            <label
              >Output token limit<input
                v-model.number="settings[provider].max_tokens"
                class="mc-input"
                type="number"
                min="1"
                max="131072"
                required
            /></label>
          </div>
          <div class="profile-fields">
            <label
              >Temperature (blank uses provider default)
              <input
                :value="settings[provider].temperature ?? ''"
                class="mc-input"
                type="number"
                min="0"
                max="2"
                step="any"
                @input="setOptionalNumber(provider, 'temperature', $event)"
              />
            </label>
            <label
              >Top P (blank uses provider default)
              <input
                :value="settings[provider].top_p ?? ''"
                class="mc-input"
                type="number"
                min="0.000001"
                max="1"
                step="any"
                @input="setOptionalNumber(provider, 'top_p', $event)"
              />
            </label>
            <label
              >Seed (blank uses provider default)
              <input
                :value="settings[provider].seed ?? ''"
                class="mc-input"
                type="number"
                min="0"
                max="2147483647"
                step="1"
                @input="setOptionalNumber(provider, 'seed', $event)"
              />
            </label>
            <label v-if="provider === 'local'"
              >Thinking<select v-model="settings.local.enable_thinking" class="mc-input">
                <option :value="null">Provider default</option>
                <option :value="true">Enabled</option>
                <option :value="false">Disabled</option>
              </select></label
            >
          </div>
        </details>
        <button
          type="button"
          class="mc-btn mc-btn-secondary"
          :disabled="
            (provider === 'gemini' && !settings.gemini_key_configured) ||
            (provider === 'openrouter' && !settings.openrouter_key_configured)
          "
          @click="test(provider)"
        >
          Test {{ providerNames[provider] }} connection
        </button>
      </fieldset>
      <p>
        Connection tests send one short completion using the values above, without saving. Each
        backup attempt counts against the job budget and uses the current call's output and sampling
        limits.
      </p>
      <p>
        Gemini quotas and rate limits depend on your AI Studio project and billing tier. Free-tier
        prompts and responses may be used to improve Google products; review the
        <a
          href="https://ai.google.dev/gemini-api/docs/pricing"
          target="_blank"
          rel="noopener noreferrer"
          >pricing and data-use terms</a
        >
        before sending campaign content.
      </p>
      <div class="actions">
        <button class="mc-btn" :disabled="busy || settings.primary === settings.backup">
          Save provider settings
        </button>
        <button type="button" class="mc-btn mc-btn-secondary" :disabled="busy" @click="load">
          Reload saved settings
        </button>
      </div>
    </form>
    <button v-else-if="!busy && !error" type="button" class="mc-btn mc-btn-secondary" @click="load">
      Load provider settings
    </button>
  </section>
</template>

<style scoped>
.llm-settings {
  margin-top: 2rem;
  border-top: 1px solid var(--mc-border);
  padding-top: 1rem;
}
fieldset {
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  margin: 1rem 0;
  padding: 1rem;
}
legend {
  font-weight: 600;
  padding: 0 0.5rem;
}
.routing-fields,
.profile-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
}
label {
  display: grid;
  gap: 0.4rem;
}
.actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}
details {
  margin: 1rem 0;
}
summary {
  cursor: pointer;
  margin-bottom: 0.75rem;
}
p {
  line-height: 1.6;
}
</style>
