# RiverLens local API · 0.2.1

Base URL: `http://127.0.0.1:8765`. All writes require JSON and the per-process
`X-RiverLens-Token`, exposed to the local UI in the root HTML meta tag
`riverlens-token`. The server enforces loopback Host and same-origin browser requests.
No endpoint executes game actions or captures a live desktop.

| Endpoint | Purpose |
| --- | --- |
| GET `/api/status` | Version, model name, API readiness, attempted calls, token usage, per-start call cap. Never returns the key. |
| GET `/api/archive` | Locally stored completed hands and replay snapshots. |
| POST `/api/import` | `{ "text": "PokerStars Hand ..." }`; validates and atomically stores completed hands, with deduplication. |
| GET `/api/session` | Saved optional personal limits or `null`; neither case requires manual budget setup. |
| POST `/api/session` | Set optional stop loss/per-hand cap, unit and starting hand ID; `{}` clears limits. No bankroll input. |
| POST `/api/recommend` | Automatically assembled study proposal for an archived replay point or explicit simulation. |
| POST `/api/analyze` | Existing equity calculator. |
| POST `/api/explain` | Optional short explanation, requires `consent: true`. |
| POST `/api/vision` | Manual archived-image transcription, requires `archived: true` and `consent: true`. |

## Optional session limits

```json
{
  "stop_loss": 200,
  "max_hand_loss": 100,
  "unit": "chips",
  "start_hand_id": "0"
}
```

No call to this endpoint is needed before recommendations. Either limit may be `null`, empty,
or omitted; numeric limits must be positive with at most two decimal places. When at least one
limit is active, its unit is required. `bankroll`, `stack`, `account_balance` and other unknown
settings fields are rejected. Existing v0.2 bankroll settings are ignored on read, preserving limits.

Units: `chips`, `USD`, `EUR`, `GBP`, `CHF`. Imported currency comes from the history header;
tournaments are treated as chips, not cash. No automatic account balance or exchange-rate lookup.

## Review a completed hand

```json
{
  "source": "archive",
  "hand_id": "100001",
  "step": 9,
  "use_ai": false
}
```

`step` is a zero-based snapshot index. Select one with `hero_decision: true`.
The server fetches the hand from its own archive. It ignores client-created profiles or equity
values. All future actions and results of the reviewed hand are excluded from model context.
`source: "demo"` selects built-in synthetic hands and reads their recorded stacks; personal limits do not apply.
For archive/demo requests, stack and unit come exclusively from the selected server snapshot.
Client overrides and earlier wins never change that stack.

To request OpenAI, set both `use_ai: true` and `consent: true`. The browser's automatic-study
checkbox supplies these for subsequent selected points. The key/model remain server-side.

## Simulated decision

```json
{
  "source": "simulation",
  "scenario": {
    "hero": "Ah Qh",
    "board": "Jh Th 2c",
    "opponents": 1,
    "pot": 20,
    "stack": 194,
    "unit": "chips",
    "hero_bet": 0,
    "current_bet": 8,
    "big_blind": 2,
    "min_raise_increment": 8,
    "invested": 6,
    "raise_reopened": true
  },
  "use_ai": false
}
```

`current_bet` is the largest total street contribution, not the cost of the call.
Call cost = current_bet − hero_bet, subject to stack. `invested` includes all prior streets
and the current street. Amounts use `unit` (default `chips`) with at most two decimal places.
Stack must be supplied for a proposal. Missing/null/empty stack produces a blocked report,
no candidates and no paid AI call; it never defaults to 200. Simulations are labeled as such,
even if the caller attempts to set a different `budget_source`.
`min_raise_increment` is the last complete bet/raise size, not the new total raise-to.
The lab defaults it to the larger of big blind/current bet if omitted; set it explicitly for
a raised pot. In the replay this is reconstructed from actual observed actions.

## Result contract

- `legal_actions`: supported action types with full `min_add` / `max_add` intervals.
- `candidates`: representative exact sizes within the rules and configured budget.
- `selected_action`: a candidate with `amount_add`, optional `raise_to`, `all_in` and `underlying`, or `null`.
- `recommendation`: `candidate_id`, `rationale`, qualitative `confidence`, `assumptions`, `source`.
- `budget`: remaining `observed_stack`, `unit`, `source` (`hand_history`, `demo_hand_history`, `simulation`),
  `account_balance: null`, earlier archive subtotal (`net_before_hand`), current investment,
  optional limits, `allowed_add`, and stop state. `configured` indicates a known stack, not manual setup.
  `unavailable` means missing/incompatible data; `blocked` means no additional chips may be committed.
  A zero allowance can still permit check/fold. No pre-hand bankroll is inferred.
- `opponents`: observed frequencies, denominators and sample quality, using local names for display.
- `equity`: reference simulation or `null` for unsupported states.
- `blocked`: a reason for abstention or `null`.
- `ai_status`: `disabled`, `skipped`, `not_configured`, `pending`, `completed`, `cached`, or `fallback`.

The API never guarantees that a selected move has positive real-world EV. The local policy is
an explicit teaching baseline; generated analysis is uncalibrated. `confidence` is not a win probability.

## Archived screenshot transcription

`/api/vision` returns `hero`, `board`, `pot`, `call`, `opponents`, `stack`, `unit`,
`account_balance`, `account_unit`, `big_blind`, `hero_bet`, `invested`, `min_raise_increment`,
`raise_reopened` and `notes`. Unreadable/absent numeric fields and units are `null`; card lists
are empty when unreadable. Money fields reject negative, non-finite, boolean or fractional-cent
values. Zero is a valid explicit observation. Unit fields are independent, using the units above.

The prompt forbids inferring account balance from table stack, buy-in, pot or winnings.
Transcription is model output requiring inspection, not a verified observation. The UI clears
missing fields, displays account balance separately, and gates automatic proposals behind
confirmation; edits invalidate that confirmation. Confirmed lab inputs remain a simulation,
not a live source. No image or balance is persisted or linked to the replay archive.

## OpenAI contract and safeguards

Responses API `POST /v1/responses`, server-side bearer key, `store: false`, 1600 output-token
limit and 50-second timeout. JSON Schema is supplied through `text.format` with `strict: true`.
`candidate_id` is an enum generated from the current budget-compliant candidates, plus `null`.
The server validates returned fields and candidate membership again before displaying a move.

Model context contains pseudonymous player IDs, numeric observed actions, profiles from
earlier hands, budget, deterministic rules and reference equity. It does not contain raw hand
text, names, future board or results. Requests are deduplicated by state, constraints, model
and prompt. Identical simultaneous requests do not cause duplicate model calls. Cache is local
process memory, up to 128 successful decisions. Failed calls are not retried automatically.

Budget violations, unknown states, timeout, refusal, invalid JSON or invalid candidate selection
cannot bypass local limits. The returned `ai_status` and reason distinguish fallback from AI output.
Changing state in the browser hides the old recommendation immediately; stale replies are ignored.

`OPENAI_MAX_CALLS` caps attempts per server start, not spending in currency. Failed attempts count.
The cap is shared by recommendations, explanations and image transcription. Paid integration
is covered with mocked responses; no live key was available for end-to-end provider testing.
