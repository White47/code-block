# On-demand ordinary symbol citation targets

Status: accepted after architecture review (2026-10-28).

Previously C6 graph navigation required a trusted already-delivered location,
even when B5 had verified an ordinary definition and the immutable code graph
contained its exact counterpart. The user had to open that graph manually.

Extend C6 with a second, explicitly planned target source. Only bound symbol
definitions of Function/Method/Class with a present but empty location fact and
complete seven-field/single-anchor code identity may plan their ordinary Symbol
root. Existing trusted locations are preferred. Nonempty bad/grouped/detail
locations, missing facts, other namespaces and identity mismatches do not gain
this fallback. Existing freshness, authority, cancellation and resource checks
continue to apply.

This is a navigation plan, not a delivered graph assertion. The Symbol builder
validates the code focus and includes it first within a positive node budget.
The frontend's existing get_view path still constructs/serializes/delivers the
real view, then C3 publishes its bounded context map with the existing CAS.
Neither capture nor navigation creates a view or fabricates a location.

No public DTO/API/dependency changes or source execution/upload. No inference
from model labels, arbitrary IDs or source positions. Unsupported candidates
retain source fallback and fail-closed navigation. Prior real native failure
and automated-CDP readiness causes remain unknown; this decision does not
retroactively identify them.
