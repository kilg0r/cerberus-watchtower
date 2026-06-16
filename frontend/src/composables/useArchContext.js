import { computed, provide } from 'vue'

/**
 * Provide the 'archCtx' lookup context that FlowInspector / FlowNode / MethodNode
 * inject for call-graph drill-through, derived from a ref/computed holding an
 * architecture payload (the shape `/api/architecture/{id}` returns).
 *
 * Shared by the live Architecture view and the git-history bundle review, so a
 * repo's tree at a past commit drills exactly like its current one.
 */
export function provideArchContext(archRef) {
  provide(
    'archCtx',
    computed(() => {
      const arch = archRef.value
      const implementations = arch?.implementations || {}
      const classMethods = arch?.class_methods || {}
      // reverse method-call index: "Type.method" -> callers. Calls to an
      // interface count for the interface and every implementation, so the
      // "called from outside" view works from either side.
      const callersOf = {}
      for (const [cls, methods] of Object.entries(classMethods)) {
        for (const method of methods) {
          for (const call of method.calls || []) {
            if (call.type === cls) continue // self calls aren't cross-references
            for (const target of [call.type, ...(implementations[call.type] || [])]) {
              const key = `${target}.${call.method}`
              ;(callersOf[key] ||= []).push({ class: cls, method: method.name })
            }
          }
        }
      }
      return {
        types: arch?.types || {},
        classDeps: arch?.class_deps || {},
        implementations,
        classSends: arch?.class_sends || {},
        classServiceUses: arch?.class_service_uses || {},
        classMethods,
        callersOf,
        handlerByRequest: Object.fromEntries(
          (arch?.handlers || []).map((h) => [h.request, h])
        ),
      }
    })
  )
}
