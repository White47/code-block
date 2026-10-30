import type { ProtocolDiagnostic } from './types';

/** Closed, text-free diagnostics; never coerce provider values for display. */
export function validProtocolDiagnostic(value: unknown): value is ProtocolDiagnostic {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const fields = Object.keys(value);
  if (!fields.includes('boundary') || fields.some(key => !['boundary', 'httpStatus', 'checkSite', 'finishReason'].includes(key))) return false;
  if (['httpStatus', 'checkSite', 'finishReason'].some(key => key in value && !fields.includes(key))) return false;
  if (!('boundary' in value)) return false;
  if (value.boundary !== 'pre_send' && value.boundary !== 'dispatch' && value.boundary !== 'result_validation') return false;
  if (fields.includes('checkSite')) {
    if (value.boundary !== 'dispatch' || fields.includes('httpStatus') || !('checkSite' in value)
        || typeof value.checkSite !== 'string' || !['response_headers', 'sse_syntax', 'chunk_shape', 'usage_shape',
          'delta_shape', 'terminal_finish', 'stream_eof', 'sdk_decode', 'context_exit', 'cleanup'].includes(value.checkSite)) return false;
    return !fields.includes('finishReason') || (value.checkSite === 'terminal_finish' && 'finishReason' in value
      && typeof value.finishReason === 'string'
      && ['length', 'content_filter', 'insufficient_system_resource', 'aborted'].includes(value.finishReason));
  }
  if (fields.includes('finishReason')) return false;
  return !fields.includes('httpStatus') || (value.boundary === 'dispatch' && 'httpStatus' in value && typeof value.httpStatus === 'number'
    && Number.isInteger(value.httpStatus) && value.httpStatus >= 100 && value.httpStatus <= 599);
}

export function copyProtocolDiagnostic(value: ProtocolDiagnostic): ProtocolDiagnostic {
  if (value.boundary === 'dispatch' && value.checkSite !== undefined) {
    return value.checkSite === 'terminal_finish'
      ? { boundary: 'dispatch', checkSite: 'terminal_finish', ...(value.finishReason === undefined ? {} : { finishReason: value.finishReason }) }
      : { boundary: 'dispatch', checkSite: value.checkSite };
  }
  if (value.boundary !== 'dispatch') return { boundary: value.boundary };
  return value.httpStatus === undefined ? { boundary: 'dispatch' }
    : { boundary: 'dispatch', httpStatus: value.httpStatus };
}
