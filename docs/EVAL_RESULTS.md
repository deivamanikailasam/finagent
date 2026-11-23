# Evaluation results

## Retrieval

Settings fixed before measuring: heading prefix on, top-k 5 / 5 / 2 per lane, abstain on an empty lane, a top score of 0 or less, or matched-IDF coverage below 0.5.

Golden labels were proposed by Claude from the policy text, without running retrieval, accepted by the owner, and committed before this evaluation.

This run follows the independent review fixes to scoring scope, tokenisation and tie-breaking. The first run, before those fixes, measured rule-source recall 26/31, with G3 and G4 as false abstentions. Templates, thresholds and labels were not changed between runs.

**Rule-source recall@5: 27/31 (87.1%)**

Requiring every pinned section of a rule: 26/31 (83.9%).

### Rule-source misses

- REQ-AMOUNT-POSITIVE (invoice_evidence): FIN-POL-008#s1 not in the top 5
- REQ-MATCHES-INVOICE (invoice_evidence): FIN-POL-001#s5 not in the top 5
- PO-REQUIRED (po_matching): FIN-POL-001#s2 not in the top 5
- NEGATIVE-LINE (invoice_evidence): FIN-POL-008#s1 not in the top 5

### Additional pinned sections not in the top 5 (information)

- REQ-MATCHES-INVOICE: FIN-POL-002#s1
- FRAUD-INDICATORS: FIN-POL-005#s4

### Golden set

| Query | Slice | Relevant | Hit@5 | RR | Abstained | False abstain | False answer | Trap above target |
|---|---|---|---|---|---|---|---|---|
| G1 | templated | FIN-POL-003#s2 | yes | 1.00 | no | no | no | n/a |
| G2 | trap_superseded_dfa | FIN-POL-003#s2 | yes | 1.00 | no | no | no | no |
| G3 | trap_injection_bank_change | FIN-POL-004#s2, FIN-POL-004#s3 | no | 0.00 | yes | yes | no | yes |
| G4 | trap_aud_75_freight | FIN-POL-002#s2 | yes | 1.00 | no | no | no | no |
| G5 | no_answer | none | n/a | n/a | yes | no | no | n/a |

Trap above target is measured on the unfiltered ranking, for information only.

### Golden misses

- G3: retrieved at rank 4, then suppressed by abstention

### Invariants

Checked at the case date and on both sides of the FIN-POL-003 effective date.

- Stale in authoritative: 0
- Non-authoritative cited: 0

### Not evaluated

- BM25 k1/b, stemming and stopwords
- the prefix on/off sweep
- top-k
- the repair count
- retry parameters (fault tests cover behaviour)
- prompt variants
- Sonnet 5.5
- synonym expansion
