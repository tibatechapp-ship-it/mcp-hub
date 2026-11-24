# Phase 2 — policy, routing and description quality

Phase 2 adds three deterministic, dependency-free layers on top of the
stateless MCP endpoint. None of them calls an LLM or the network.

## Tool policy gateway

`POST /mcp` evaluates every `tools/call` through a ZYKO-compatible JSON policy
before the handler runs. Configure exactly one source:

| Variable | Meaning |
| --- | --- |
| `MCP_HUB_POLICY_FILE` | Path to a JSON policy file |
| `MCP_HUB_POLICY_JSON` | Inline JSON policy |
| `MCP_HUB_POLICY_AUDIT` | Optional JSONL audit path override |

A minimal policy:

```json
{
  "version": 1,
  "name": "office-laptop",
  "tools": {"allow": ["search_*", "query_catalog"], "deny": ["doctor_report"]},
  "privacy": {"redact": ["email", "ssn", "secret"]},
  "audit": {"enabled": true}
}
```

`tools.deny` wins over `tools.allow`; both use shell-style `fnmatch` patterns.
Redaction is applied recursively to tool arguments; `secret` reuses mcp-hub's
existing secret rules, and `email`, `ssn`, `iban`, `phone` use explicit patterns.
`privacy.custom` accepts `{"pattern": "...", "replace": "[REDACTED]"}` entries.

Unsupported ZYKO controls (`models`, `providers`, `budgets`, `rate_limit`, …)
are **rejected**, not ignored, so a policy cannot appear protective while doing
nothing. A denial returns JSON-RPC `-10002` (`POLICY_DENIED`); an invalid policy
fails closed with the same code. `mcp-hub policy-snapshot` shows the active gate
without audit paths or regex bodies.

## Bilingual semantic router

`route_task` ranks catalog tools for an Arabic, English or mixed-language task.
It normalizes Arabic letters and diacritics, drops stopwords, expands a small
reviewable synonym map, and scores tool names, descriptions, tags and task
profiles. `compact=true` truncates descriptions and omits input schemas so a
client can fit more candidates into a small context. No translation is invented:
Arabic descriptions come from the bundled registry, English ones from tool
contracts.

```bash
mcp-hub route-task "ابحث عن خادم sql" --compact --limit 3
```

## Six description smells

`description_smell_audit` and `mcp-hub description-smells` audit bundled
descriptions. The codes are stable and diffable:

| Code | Meaning |
| --- | --- |
| `S1_EMPTY` | Empty description |
| `S2_TOO_SHORT` | Shorter than 20 characters |
| `S3_PLACEHOLDER` | TODO/TBD/lorem ipsum or Arabic placeholder |
| `S4_SECRET_SHAPED` | Redaction rule or long token-shaped string matched |
| `S5_VAGUE` | Generic text such as "tool" or "اداة عامة" |
| `S6_MIXED_LANGUAGE` | Arabic and unexplained English words in one field |

There is no invented quality score: a report is a list of codes plus short
evidence snippets.

## Mirrored-token guard

The stateless core rejects `Mcp-Param-*` headers whose name contains
`token`, `api_key`, `apikey`, `authorization`, `credential`, `password`,
`passwd` or `secret`. Credentials belong in the JSON body/policy path, never in
mirrored headers. Such a request returns `-32020` (`HEADER_MISMATCH`) with HTTP
400.
