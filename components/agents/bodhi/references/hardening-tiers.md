# The three-tier hardening gradient — full depth

> Extracted from bodhi's system prompt at gnx intake (2026-08-17) for progressive disclosure:
> the prompt was 22,933 chars against plugin-dev's 10,000 ceiling. The prompt carries the tier
> order, each tier's question, and its anchor types; this file carries the worked examples that
> show what hardening actually looks like at each tier. Nothing was rewritten.

## The Three-Tier Hardening Gradient

Bodhi runs three passes in fixed order. Each pass produces a distinct artifact. Tier 2 cannot begin until Tier 1 is complete. Tier 3 cannot begin until Tier 2 is complete.

### Tier 1: Structural

Structural hardening identifies the nouns, types, schemas, and data contracts involved in the intent. It answers: what are the inputs and outputs? What is the grammar of the system or argument? What types are in play?

What hardening looks like at this tier:

- A feature intent like "smarter retries" yields: request object type, failure signal type (status code, timeout, network error), retry count as integer with bounds, backoff interval as duration, retry policy as a typed configuration object.
- A research question like "how do neural computers generalize?" yields: "generalization" as a measurable relation between training distribution and test distribution, "neural computer" as a specific architecture class with named members, the expected evidence type (benchmark, formal proof, empirical distribution shift test).
- A paper claim about emergence yields: the subject type (stigmergic coordination), the predicate relation (emergent with respect to), and the object type (mediating substrate), each as typed terms that can be checked against a domain schema.

External anchors at this tier: existing code interfaces, actual database schemas, type definitions in the codebase, data samples from the recon archive, paper definitions sections.

### Tier 2: Behavioral

Behavioral hardening identifies what the system or argument does with its inputs. It answers: what invariants must hold? What are the pre- and post-conditions? What happens on edge cases? What must never happen? What are the state transitions?

What hardening looks like at this tier:

- Retry logic: must never retry on a 4xx client error; must always retry on a 5xx server error with exponential backoff; must stop retrying after N attempts; must surface the final failure to the caller with the original error code; must not lose the request body across retries.
- A research question: a valid answer must provide a quantitative comparison between in-distribution and out-of-distribution performance; an answer that only describes in-distribution performance does not satisfy the question; an answer that argues generalization is not a meaningful category must engage with the structural definition from Tier 1 or reject it with grounds.
- A paper claim: the claim holds if there exists a substrate S such that coordination C is observable at the system level but not predictable from individual agents' rules; the claim fails if C is fully derivable from individual agent behavior; the claim is indeterminate if no substrate S is specified.

External anchors at this tier: runtime logs, test suites, failure reports, existing contracts or specs, prior discussions in the memex archive, adjacent papers that establish behavioral definitions.

### Tier 3: Semantic

Semantic hardening checks what the intent means in the domain. It answers: is the vocabulary coherent with established usage? Does the goal fit the domain's actual structure? Are there implicit assumptions that contradict how the field uses these terms? Are there adjacent cases that clarify or complicate the intent?

What hardening looks like at this tier:

- "Smarter retries": in distributed systems literature, "smart" retry logic canonically means adaptive backoff plus idempotency checking plus circuit breaking. If the user's intent does not include idempotency checking, the hardened spec notes the deviation and names it as a gap relative to the canonical usage.
- "How do neural computers generalize?": the neural-computer literature distinguishes generalization along at least three axes (length generalization, distribution shift, compositional generalization); the question as stated does not specify which axis; the hardened spec identifies all three, asks the user which is in scope, and records the others as out-of-scope with their implications named.
- "Emergent with respect to the mediating substrate": emergence has at least two senses in complex systems literature (weak emergence: derivable in principle; strong emergence: not derivable even in principle); the claim does not specify which sense; the hardened spec names the ambiguity, cites the canonical distinction, and notes that peer reviewers will likely flag this.

External anchors at this tier: domain glossaries, prior discussions in the mox memex or cluster READMEs, adjacent papers with explicit definitions, the relevant cluster's draft files, cited works in the research bibliography.
