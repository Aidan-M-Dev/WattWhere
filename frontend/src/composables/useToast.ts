/**
 * FILE: frontend/src/composables/useToast.ts
 * Role: Shared toast notification state + push/remove helpers.
 *       Module-level ref so all consumers share the same toast list.
 * Agent boundary: Frontend — composable (P2-13)
 */

import { ref } from 'vue'

export interface Toast {
  id: string
  message: string
  type: 'error' | 'warning' | 'info'
}

const MAX_VISIBLE = 3
const AUTO_DISMISS_MS = 4000

const toasts = ref<Toast[]>([])

export function useToast() {
  function push(toast: Toast) {
    // Deduplicate by id
    if (toasts.value.some(t => t.id === toast.id)) return

    // Enforce max visible — evict oldest first
    while (toasts.value.length >= MAX_VISIBLE) {
      toasts.value.shift()
    }

    toasts.value.push(toast)

    // Auto-dismiss non-warning toasts after 4s
    if (toast.type !== 'warning') {
      setTimeout(() => remove(toast.id), AUTO_DISMISS_MS)
    }
  }

  function remove(id: string) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  return { toasts, push, remove }
}
