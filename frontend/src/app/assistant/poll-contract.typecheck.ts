// Compile-time coverage for the existing C7 closed event contract. No runtime
// values or provider data are constructed by these assertions.
import type { ConnectionPoll, ExplanationPoll, NavigationPoll } from './types';

type Expect<T extends true> = T;
type Rejects<Event, Candidate> = Candidate extends Event ? false : true;
type Accepts<Event, Candidate> = Candidate extends Event ? true : false;
type IdentityKeys = 'kind' | 'jobId' | 'projectEpoch' | 'analysisGeneration'
  | 'conversationId' | 'conversationRevision';
type NavigationIdentity = Pick<NavigationPoll, IdentityKeys>;
type ConnectionIdentity = Pick<ConnectionPoll, IdentityKeys>;
type ExplanationIdentity = Pick<ExplanationPoll, IdentityKeys>;

export type PollContractAssertions = [
  Expect<Rejects<NavigationPoll['events'][number], NavigationIdentity & {
    seq: number; type: 'usage'; usage: ConnectionPoll['usage']; }>>,
  Expect<Rejects<ConnectionPoll['events'][number], ConnectionIdentity & {
    seq: number; type: 'display_text'; text: string; }>>,
  Expect<Rejects<ExplanationPoll['events'][number], ExplanationIdentity & {
    seq: number; type: 'tool'; sourceText: string; }>>,
  Expect<Accepts<NavigationPoll['events'][number], NavigationIdentity & {
    seq: number; type: 'status'; status: 'Running'; }>>,
];
