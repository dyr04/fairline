# DECISIONS.md — shared memory between agents (plan §7 standing rule 6)

Agents share NO memory. Read this before writing code; append every interface
decision after. Plan reference: devig-arb-project-plan.md.

## DECISIONS
- Canonical timestamp format (ALL tables, ALL writers): `%Y-%m-%dT%H:%M:%SZ`
  via `src.db.utc_now_str()` / `canonical_ts()` ONLY. Regex-checked in verifies.
- Data-file naming contract: `raw/{YYYY-MM-DD}_{sport}.jsonl.gz` on the `data`
  branch, indexed by `manifest.json` at the branch root (appended every T5
  commit). T7 discovers files ONLY via manifest.json.
- Grouping contract: devig consumes (event_id, book, snapshot_batch_id) groups
  ONLY; incomplete groups are skipped. Never group by pulled_at.
- Canonical book/team space: The Odds API keys, via src/mapping.py. All
  providers map INTO this space (T13).
- human_round implemented as floor-to-increment: strictly never exceeds
  computed Kelly (satisfies the plan <=105% bound; named case 98.34 -> 95).
- Closing line: DERIVED as last complete pre-commence group per (event, book),
  quality-tiered tight(<20m)/acceptable(20-45m)/stale(>45m); stale excluded
  from headline CLV.
- Repo must be PUBLIC (Streamlit Cloud reads data-branch raw URLs).
- Model routing per plan §7: Haiku(T1,T6,T10,verify boilerplate),
  Sonnet(T2-T5,T7,T12,T13), Frontier(T8,T11,reviews,T14).

## HANDOFF NOTES
- dashboard/data_loader.py: set REPO_RAW after the repo exists (T5/T7).
- verify_t5/t6/t7/t12 require the live environment (repo, key, webhook).
