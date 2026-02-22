<!--
  FILE: frontend/src/components/OnboardingTooltip.vue
  Role: First-time visitor onboarding overlay — explains the platform, tile resolution,
        and data centre siting use case. Shown once per browser (localStorage flag).
  Agent boundary: Frontend — onboarding
-->
<template>
  <Transition name="onboarding-fade">
    <div v-if="!hasSeenTooltip" class="onboarding-overlay" @click.self="dismiss">
      <div class="onboarding-card">
        <h2 class="onboarding-card__title">Ireland Data Centre Suitability</h2>
        <ul class="onboarding-card__list">
          <li>Score ~14,000 grid tiles across 5 thematic sorts — switch sorts to compare factors.</li>
          <li>Tiles are ~5 km² — designed for regional analysis, not site-level precision.</li>
          <li>Click any tile to see a detailed breakdown in the sidebar.</li>
        </ul>
        <button class="onboarding-card__btn" @click="dismiss">Got it</button>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const hasSeenTooltip = ref(localStorage.getItem('onboarding_seen') === 'true')

function dismiss() {
  localStorage.setItem('onboarding_seen', 'true')
  hasSeenTooltip.value = true
}
</script>

<style scoped>
.onboarding-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
}

.onboarding-card {
  max-width: 440px;
  width: 90%;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 28px 32px;
  color: var(--color-text);
}

.onboarding-card__title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 16px;
}

.onboarding-card__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 24px;
  padding: 0;
}

.onboarding-card__list li {
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text-muted);
  padding-left: 16px;
  position: relative;
}

.onboarding-card__list li::before {
  content: '•';
  position: absolute;
  left: 0;
  color: var(--color-accent);
}

.onboarding-card__btn {
  display: block;
  width: 100%;
  padding: 10px 0;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  color: #000;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}

.onboarding-card__btn:hover {
  opacity: 0.85;
}

/* Fade transition */
.onboarding-fade-enter-active,
.onboarding-fade-leave-active {
  transition: opacity 0.2s ease;
}
.onboarding-fade-enter-from,
.onboarding-fade-leave-to {
  opacity: 0;
}
</style>
