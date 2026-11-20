# SZSE anomaly forensics

This tool investigates the existing bounded chain-audit outputs. It does not
crawl all announcements and does not modify source reports.

## Run locally first (no network)

```powershell
python -u .\scripts\szse_anomaly_forensics.py `
  --report-dir .\reports\szse_year_boundary_audit `
  --output-dir .\reports\szse_anomaly_forensics
```

Review the generated JSON and Markdown before considering any network replay.

## Optional GET replay

```powershell
python -u .\scripts\szse_anomaly_forensics.py `
  --report-dir .\reports\szse_year_boundary_audit `
  --output-dir .\reports\szse_anomaly_forensics_fetch `
  --fetch --delay 0.5 --max-requests 60
```

Replay is opt-in. Only GET URLs discovered in the saved request manifest are
considered. Only HTTPS hosts equal to szse.cn or ending in .szse.cn, standard
port, and HTML resources are eligible. Redirect destinations are validated
before following. PDF resources are not fetched.

## Known PDF resources in the supplied diagnostic

| File | Identified title | Correct interpretation |
|---|---|---|
| P020201231733275849449.pdf | 2020 年深圳证券市场概况 | SZSE-hosted annual market overview PDF |
| P020251231674893217325.pdf | 2025 年深圳证券市场概况 | SZSE-hosted annual market overview PDF |

These two URLs are on the SZSE domain. They are not external-domain URLs and
are not HTML announcement pages. The audit should distinguish:

1. **Host trust**: the resource is hosted on an approved SZSE domain.
2. **Resource type**: PDF, not HTML.
3. **Graph semantics**: a PDF linked from an announcement is a document/resource
   edge, not automatically a pre/next announcement node.

Therefore, these findings should not be described as malicious or external
URLs. They should be retained as linked annual-market-overview resources and
excluded from the announcement-node chain unless independent source evidence
proves otherwise. This is likely an audit-rule/resource-type false positive,
not a confirmed SZSE data error.

## Reading the v1.1 output

The extractor now inspects nested objects and list values for issue labels,
identifiers, and URL candidates, rather than assuming that every report uses
the same top-level field names.

It reports two separate URL categories:

- `unsafe_url_evidence`: URLs that fail the HTTPS/SZSE-host policy. A trusted
  SZSE PDF is not placed here merely because it is not HTML.
- `linked_resource_evidence`: SZSE-hosted PDF and other non-HTML resources,
  kept separate from announcement-page nodes.

The generated JSON retains each finding's source file and JSON location so it
can be traced back to the original audit outputs. Compare the extracted
duplicate-ID groups and resource links with the original diagnostic and
hard-failure reports; extraction counts alone are not proof that an anomaly is
resolved.

## Evidence count semantics (v1.2)

The extractor counts explicit anomaly records rather than inferring an anomaly
whenever an arbitrary parent object happens to contain both an ID and URLs.
This prevents unrelated child records from being combined into synthetic
duplicate-ID findings.

- `duplicate_id_evidence` is grouped by the duplicated document/article ID.
  Multiple source-report occurrences for the same ID are retained in
  `source_locations` and `evidence_record_count`.
- `unsafe_url_evidence` contains URLs explicitly attached to
  `unsafe_neighbor_url` findings and classified as unsafe or untrusted.
- `linked_resource_evidence` keeps trusted SZSE-hosted PDFs separately. Its
  identity is the source ID + target ID + resolved resource URL, so repeated
  report copies do not inflate the number of unique resource links.
- Relation edges are extracted from each object containing its own
  source/target/relation fields; parent and child fields are not merged.

A changed count is a diagnostic signal, not proof of correctness. Compare the
source locations and URLs against the original diagnostic and hard-failure
records before assigning final classifications.

## Source-marker versus URL-policy findings (v1.3)

The audit label `unsafe_neighbor_url` is evidence that the source audit flagged
a neighbor link. It is not, by itself, proof that the URL is external or unsafe.

The v1.3 extractor therefore separates:

- `unsafe_issue_records`: source records carrying the
  `unsafe_neighbor_url` marker.
- `unsafe_url_evidence`: candidate URLs from those records that actually fail
  the HTTPS/SZSE-host policy.
- `source_rule_conflict_candidates`: URLs marked unsafe by the source report
  that nevertheless pass the host and HTTPS policy; these require review of
  the resource type and graph semantics.
- `linked_resource_evidence`: trusted SZSE-hosted PDF links, retained as
  resources rather than announcement HTML nodes.

URL extraction is restricted to explicit URL fields and diagnostic text fields;
arbitrary strings elsewhere in an anomaly subtree are not treated as URL
evidence. The summary now reports issue-record counts separately from unique
ID groups and unique resource links.

Do not compare the previous v1.2.3 `unsafe_url_evidence=25` directly with
the source audit's six `unsafe_neighbor_url` findings: the former was polluted
by over-broad subtree extraction and by treating every non-PDF URL in a marked
record as unsafe. Re-run v1.3 and inspect the actual URLs and source locations.

## v1.4 issue scoping and de-duplication

The v1.3 report exposed a parent-container false positive: the root
`diagnostic_report.json` object inherited an issue marker from aggregate
metadata, then combined unrelated descendant IDs and URLs. v1.4 changes marker
recognition to exact issue-type/code values or exact marker keys. Summary
containers are not issue records merely because their field names contain an
issue marker.

For explicitly marked issue objects, URL extraction may inspect scalar strings
inside that object. It does not cross into parent or sibling records. The
extractor now keeps both:

- `issue_type_counts`: unique issue groups after canonical de-duplication.
- `issue_type_occurrence_counts`: raw record occurrences across the report
  files, which may repeat the same issue in diagnostic, summary, and issue-list
  outputs.

`unsafe_issue_records` and `duplicate_issue_records` include grouped source
locations and occurrence counts. `unsafe_url_evidence` still means URLs that
fail the HTTPS/SZSE policy, not all source-marked links. Trusted SZSE PDF links
remain in `linked_resource_evidence`, keyed by source ID, target ID, and URL.

For duplicate-ID findings, inspect each group's `urls` and
`url_evidence_status`. An empty or single-URL list is incomplete evidence and
must remain unresolved; it must not be automatically classified as a URL
alias or a confirmed ID collision.

### v1.4.1 marker guard

A numeric aggregate such as `unsafe_neighbor_url: 25` is not an issue record.
Exact marker-key recognition therefore accepts non-empty string/object/list
values, while explicit type/code fields must contain an exact normalized marker
value. This prevents numeric counters from turning a report root into an
anomaly record.

## v1.4.2 aggregate-container exclusion

Objects containing aggregate collection fields such as `audit_summary`,
`hard_failures`, `hard_failure_issues`, `issue_type_counts`, `issues`,
`nodes`, or `edges` are treated as report containers, not issue records.
Their child records are still traversed independently. The generated JSON
lists these paths in `excluded_aggregate_containers`.

For relation issues with source and target IDs, de-duplication uses the issue
type, source ID, target ID, relation, and returned reverse ID. URL evidence is
retained on the grouped record but does not split one relation issue into
multiple groups solely because report copies serialize URL fields differently.

### v1.4.3 evidence union

When repeated report copies collapse into one semantic issue group, the grouped
record now carries the union of URLs and IDs observed across those copies,
along with distinct source locations and an occurrence count. De-duplication
therefore no longer discards URL evidence from later copies.

## Classification discipline

Use only these final classifications after examining raw HTML, JSON and
relation API evidence:

- confirmed_data_error
- historical_channel_transition
- url_alias
- parser_error
- audit_rule_false_positive
- unresolved

The script extracts evidence; it does not automatically assign a final
classification where the source evidence is insufficient.
