# Live model output (labelled)

Real call to `claude-haiku-5-5` on FIN-001, at 2026-11-23T05:55:37.135298+00:00. Redacted before storage. It passed the validator on the first try.

Input tokens: 4016. Output tokens: 2412. The model explains the outcome; it did not decide it.

| # | Claim | Citations | Number IDs |
|---|---|---|---|
| 1 | The fixed outcome for this case is APPROVE_FOR_POSTING, and this explanation does not change it. | FIN-POL-001#s3 |  |
| 2 | Under the policy, an AP recommendation is not an approval, and the system must validate delegated authority under the DFA matrix before posting. | FIN-POL-001#s3, FIN-POL-003#s2 |  |
| 3 | The next action is to submit the case for approval under the DFA matrix, and the owner is AP Operations. | FIN-POL-003#s2 |  |
| 4 | The invoice gross amount of 9900.00 matches the PO total of 9900.00 for PO-4471. | PO:PO-4471, FIN-POL-002#s1 | FACT-INVOICE-GROSS, FACT-PO-TOTAL |
| 5 | Line 1 has 40 units received and a line total of 6000.00, with a recorded line variance of 0.00. Line 2 has 100 units received and a line total of 3000.00, with a recorded line variance of 0.00. | CASE:goods_receipt_note, FIN-POL-002#s2 | FACT-RECEIPT-1, FACT-RECEIPT-2, LINE-1-TOTAL, LINE-2-TOTAL, CALC-VARIANCE-L1, CALC-VARIANCE-L2 |
| 6 | The invoice tax of 900.00 is shown separately from the subtotal of 9000.00, so tax is not hidden in price variance. | FIN-POL-002#s2 | FACT-INVOICE-TAX, CALC-SUBTOTAL |
| 7 | The invoice currency is AUD, which matches the PO currency, so no currency conversion issue is recorded. | FIN-POL-009#s1 |  |
| 8 | Vendor V-100 has ACTIVE status. Under the policy, an ACTIVE vendor may still require additional controls under the higher-risk transaction rules. | VENDOR:V-100, FIN-POL-004#s4, FIN-POL-003#s3 |  |
| 9 | Duplicate detection is required for every invoice. No duplicate exception is recorded for this case. | FIN-POL-005#s1 |  |
| 10 | No exceptions are recorded and no unknowns are recorded for this case. The primary category is none. | FIN-POL-001#s5 |  |
