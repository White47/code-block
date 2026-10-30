'use strict';

// The approved single implementation is CommonJS and uses the existing peer.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const React = require('react');

function useComposedRef(internalRef, forwardedRef) {
  // Each memoized callback owns its captured refs; render never mutates ownership.
  return React.useMemo(() => {
    let active = null;

    function release(record) {
      if (record.released) return;
      record.released = true;
      if (active === record) active = null;
      const entries = record.entries;
      record.entries = null;
      let failed = false;
      let failure;
      for (const entry of entries) {
        try {
          if (typeof entry.ref === 'function') {
            if (entry.cleanup) entry.cleanup();
            else entry.ref(null);
          } else {
            entry.ref.current = null;
          }
        } catch (error) {
          if (!failed) { failed = true; failure = error; }
        }
      }
      if (failed) throw failure;
    }

    return node => {
      if (active) release(active);
      if (node === null) return;
      const record = { released: false, entries: [] };
      active = record;
      const refs = internalRef === forwardedRef
        ? [internalRef] : [internalRef, forwardedRef];
      try {
        for (const ref of refs) {
          if (ref == null) continue;
          if (typeof ref === 'function') {
            const cleanup = ref(node);
            // Only successfully acquired callbacks participate in rollback.
            record.entries.push({ ref, cleanup: typeof cleanup === 'function' ? cleanup : null });
            if (cleanup !== undefined && typeof cleanup !== 'function') {
              throw new TypeError('Ref callbacks must return void or a cleanup function');
            }
          } else {
            // Capture before assignment so a throwing setter can also be rolled back.
            record.entries.push({ ref, cleanup: null });
            ref.current = node;
          }
        }
      } catch (error) {
        try { release(record); } catch { /* Setup failure takes precedence. */ }
        throw error;
      }
      return () => release(record);
    };
  }, [internalRef, forwardedRef]);
}

module.exports = useComposedRef;
