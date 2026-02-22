<!--
  FILE: frontend/src/components/ToastContainer.vue
  Role: Renders active toast notifications — fixed top-right overlay.
        Max 3 visible at once (useToast enforces). Each toast has a dismiss button.
  Agent boundary: Frontend — UI overlay (P2-13)
  Dependencies: useToast composable
-->
<template>
  <div class="toast-container" v-if="toasts.length" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="toast"
        :class="`toast--${toast.type}`"
        role="alert"
      >
        <span class="toast__message">{{ toast.message }}</span>
        <button
          class="toast__dismiss"
          aria-label="Dismiss"
          @click="remove(toast.id)"
        >
          <X :size="14" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'

const { toasts, remove } = useToast()
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 360px;
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  color: #fff;
  pointer-events: auto;
  backdrop-filter: blur(8px);
}

.toast--error {
  background: rgba(220, 53, 69, 0.92);
}

.toast--warning {
  background: rgba(217, 149, 22, 0.92);
}

.toast--info {
  background: rgba(59, 130, 246, 0.92);
}

.toast__message {
  flex: 1;
  line-height: 1.35;
}

.toast__dismiss {
  flex-shrink: 0;
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.8);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.12s;
}

.toast__dismiss:hover {
  color: #fff;
}

/* Slide-in / fade-out transitions */
.toast-enter-active {
  transition: all 0.25s ease-out;
}

.toast-leave-active {
  transition: all 0.2s ease-in;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(40px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(40px);
}
</style>
