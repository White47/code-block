import { BridgeError } from '../../api/bridge';
import type { TextKey, Translate } from '../i18n';
import { assistantErrorCodes } from './poll';
import { validProtocolDiagnostic } from './diagnostic';

export function assistantIssue(error: unknown, t: Translate): string {
  const code = error instanceof BridgeError ? error.code
    : typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string' ? error.code : '';
  if (!assistantErrorCodes.has(code)) return t('aiBridgeError');
  const message = `${code}: ${t(code as TextKey)}`;
  if (code !== 'AI_PROTOCOL_INCOMPATIBLE' || typeof error !== 'object' || error === null
      || !('diagnostic' in error) || !validProtocolDiagnostic(error.diagnostic)) return message;
  const diagnostic = error.diagnostic;
  const labels = { pre_send: 'aiProtocolPreSend', dispatch: 'aiProtocolDispatch',
    result_validation: 'aiProtocolResultValidation' } as const;
  const sites = { response_headers: 'aiCheckHeaders', sse_syntax: 'aiCheckSse', chunk_shape: 'aiCheckChunk',
    usage_shape: 'aiCheckUsage', delta_shape: 'aiCheckDelta', terminal_finish: 'aiCheckFinish',
    stream_eof: 'aiCheckEof', sdk_decode: 'aiCheckSdk', context_exit: 'aiCheckExit', cleanup: 'aiCheckCleanup' } as const;
  const reasons = { length: 'aiFinishLength', content_filter: 'aiFinishFilter',
    insufficient_system_resource: 'aiFinishResource', aborted: 'aiFinishAborted' } as const;
  return `${message} · ${t(labels[diagnostic.boundary])}${diagnostic.httpStatus === undefined
    ? '' : ` · HTTP ${diagnostic.httpStatus}`}${diagnostic.checkSite === undefined
    ? '' : ` · ${t(sites[diagnostic.checkSite])}`}${diagnostic.finishReason === undefined
    ? '' : ` · ${t(reasons[diagnostic.finishReason])}`}`;
}
