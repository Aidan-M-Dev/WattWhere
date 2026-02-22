// @vitest-environment happy-dom

/**
 * Unit tests for ToastContainer + useToast composable.
 *
 * Tests cover: rendering on push, deduplication, auto-dismiss timing,
 * and warning persistence (no auto-dismiss).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ToastContainer from '@/components/ToastContainer.vue'
import { useToast } from '@/composables/useToast'

// ── Mock lucide-vue-next ─────────────────────────────────────────
vi.mock('lucide-vue-next', () => ({
  X: { template: '<span data-testid="icon-x" />' },
}))

// ── Helpers ──────────────────────────────────────────────────────

function mountContainer() {
  return mount(ToastContainer)
}

// ── Tests ────────────────────────────────────────────────────────

describe('ToastContainer + useToast', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // Clear any lingering toasts between tests
    const { toasts } = useToast()
    toasts.value = []
  })

  it('renders a toast when push() is called', async () => {
    const wrapper = mountContainer()
    const { push } = useToast()

    push({ id: 'test-1', message: 'Something went wrong', type: 'error' })
    await nextTick()

    const toastEls = wrapper.findAll('.toast')
    expect(toastEls.length).toBe(1)
    expect(toastEls[0].text()).toContain('Something went wrong')
    expect(toastEls[0].classes()).toContain('toast--error')
  })

  it('does not duplicate toasts with the same id', async () => {
    const wrapper = mountContainer()
    const { push } = useToast()

    push({ id: 'dup', message: 'Error A', type: 'error' })
    push({ id: 'dup', message: 'Error A again', type: 'error' })
    await nextTick()

    const toastEls = wrapper.findAll('.toast')
    expect(toastEls.length).toBe(1)
  })

  it('auto-dismisses error toasts after 4000ms', async () => {
    vi.useFakeTimers()

    const wrapper = mountContainer()
    const { push } = useToast()

    push({ id: 'auto', message: 'Will disappear', type: 'error' })
    await nextTick()
    expect(wrapper.findAll('.toast').length).toBe(1)

    // Advance time past the 4s auto-dismiss
    vi.advanceTimersByTime(4100)
    await nextTick()

    expect(wrapper.findAll('.toast').length).toBe(0)

    vi.useRealTimers()
  })

  it('does not auto-dismiss warning toasts', async () => {
    vi.useFakeTimers()

    const wrapper = mountContainer()
    const { push } = useToast()

    push({ id: 'warn', message: 'No internet', type: 'warning' })
    await nextTick()
    expect(wrapper.findAll('.toast').length).toBe(1)

    // Advance well past the auto-dismiss window
    vi.advanceTimersByTime(10000)
    await nextTick()

    // Warning should still be visible
    expect(wrapper.findAll('.toast').length).toBe(1)
    expect(wrapper.find('.toast--warning').exists()).toBe(true)

    vi.useRealTimers()
  })

  it('removes a toast when dismiss button is clicked', async () => {
    const wrapper = mountContainer()
    const { push } = useToast()

    push({ id: 'dismiss-me', message: 'Click to close', type: 'info' })
    await nextTick()
    expect(wrapper.findAll('.toast').length).toBe(1)

    await wrapper.find('.toast__dismiss').trigger('click')
    await nextTick()

    expect(wrapper.findAll('.toast').length).toBe(0)
  })
})
