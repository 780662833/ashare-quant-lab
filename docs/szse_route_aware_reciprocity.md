# SZSE bounded-capture route-aware reciprocity audit

## Purpose and status

This is an offline audit of every relation edge contained in one supplied SZSE
chain capture. It does not crawl SZSE, issue network requests, modify the input
archive, infer missing edges, or qualify the complete historical SZSE graph.

The audit avoids document-ID-only route matching. Each edge is checked against
the exact target URL recorded by the source neighbor response. The target's
route-specific HTML navigation channel is kept separate from its JSON business
data channel. Reciprocity requires both the reverse document ID and the exact
reverse URL to point back to the source route.

Implementation:

- `src/ashare_quant/research/szse_route_aware_reciprocity.py`
- `scripts/audit_szse_route_aware_reciprocity.py`
- `tests/unit/test_szse_route_aware_reciprocity.py`

## Usage

Run from the repository root against the original ZIP archive and the
operator-supplied exact-route report:

```powershell
python scripts\audit_szse_route_aware_reciprocity.py `
  --report-dir reports\szse_year_boundary_audit.zip `
  --route-context-report reports\source_probes\szse_route_context\route_context_report.json `
  --output-dir reports\source_probes\szse_route_aware_reciprocity
```

`--report-dir` may instead point to the extracted archive directory containing
`nodes.json`, `edges.json`, `manifest.json` and `neighbor_responses/`.
The output directory must be outside an extracted source directory.

The command is local-only. It makes zero HTTP requests and writes only the two
new output files under `--output-dir`:

- `route_aware_reciprocity_report.json`: structured audit, input digests,
  per-edge decisions, counts and explicit limitations;
- `route_aware_reciprocity_edges.csv`: one row per input edge for review and
  filtering.

## Evidence checks

For each edge, the audit performs these checks in order:

1. **Source node and source-route identity.** The source ID must identify one
   captured node. Its exact route URL must be an HTTPS URL on the expected
   SZSE host and its route ID must match the node ID. Captured HTML article ID
   and JSON document ID must agree with that ID.
2. **Source request and neighbor evidence.** The original request manifest
   must be internally consistent and GET-only. The source route must have one
   successful HTML request, same-path JSON request, and neighbor API request
   whose channel ID is the source page's HTML navigation channel. The saved
   neighbor response must have the corresponding `chnlDocId`, and its relation
   field's ID and URL must equal the edge's target ID and exact target URL.
3. **Target exact-route context.** The target is resolved only by exact URL,
   never by document ID alone. It can be resolved from an original captured
   node or from the supplemental exact-route report. A supplemental route is
   accepted only when HTML `articleId` and JSON `docId` match the URL ID, the
   reference comparison reports `same_business_payload` with matching ID,
   title, publication time and content SHA-256, the neighbor channel matches
   the HTML navigation channel, and the three exact GET receipts are
   successful and unchanged.
4. **Reverse relation.** For a source `pre` relation, the exact target route's
   `nextDocId` and `nextDocPuburl` must point back to the source ID and exact
   source URL. For a source `next` relation, the target's `preDocId` and
   `preDocPuburl` must point back. Matching only the ID is insufficient.

Missing, duplicate, conflicting, malformed or failed route evidence is kept
unresolved. A route-specific reverse ID or URL mismatch is reported separately
from missing evidence. The audit does not label a mismatch as an SZSE source
data error; that requires separate source adjudication.

## 2026-11-20 bounded-sample result

The supplied `szse_year_boundary_audit.zip` sample contains 66 captured nodes,
77 edge records and 198 request-manifest records (66 each for HTML, detail JSON
and neighbor API). The original capture contains exact target-route contexts
for 74 edges. Three remaining edge targets require the supplemental exact-route
observations for IDs 584083 and 584085 recorded in
`reports/source_probes/szse_route_context/route_context_report.json`.

The route-aware audit result for this bounded edge set is:

| Check | Result |
|---|---:|
| Unique source/relationship/target edges | 77 |
| Source edge observation ID + URL match | 77 / 77 |
| Target exact-route context available | 77 / 77 |
| Reverse ID and exact URL both match | 77 / 77 |
| Non-reciprocal or unresolved edges | 0 |
| Target context from original archive | 74 |
| Target context from supplemental exact-route report | 3 |

The bounded edge-set reciprocity acceptance is true for these 77 edges. This
result is limited to the files and records in the supplied capture and the two
supplemental routes. It does not prove archive completeness, historical
universe coverage, global ID uniqueness, cross-period chain continuity, or
correctness of events not represented in the sample.

The separate `618392` URL-path date versus publication-date anomaly remains
outside this reciprocity result and must retain its own classification. The
v6 forensic counter is not changed by this audit.

## Acceptance boundary

The output always sets:

- `full_graph_acceptance = false`
- `production_admitted = false`
- `pit_admitted = false`
- `training_admitted = false`
- `backtest_admitted = false`

A bounded-edge-set PASS is not a full historical graph PASS and does not
promote the source into a production event dataset or any research-data gate.
