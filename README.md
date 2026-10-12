# RiverLens

**Offline poker hand replay, equity analysis and AI-powered review.**

A small local study room for completed Texas Hold’em hands and simulated scenarios.
Python 3.10+ and a browser. No pip, npm, database setup or build step required.
The initial interface is in Italian.

## Start

```sh
cd riverlens
python3 server.py
```

Your browser opens at http://127.0.0.1:8765. Keep the terminal open; stop with Ctrl+C.
On macOS, you can also double-click `Start-RiverLens.command`. If it is not executable:

```sh
chmod +x Start-RiverLens.command
```

On Windows, install Python 3.10+ and run `start-windows.bat`.
If the port is occupied: `python3 server.py --port 8766`.

## Try it

1. With an empty archive, the app loads two **synthetic example hands**, with a clearly marked fictional chip budget.
2. Select a hand and move to a Hero decision. The study proposal, amount, legal action bounds, equity and budget update automatically.
3. Import completed English PokerStars `.txt` histories. Hands are archived locally and duplicate imports do not inflate statistics.
4. Set **Budget → Configura** once for imported hands. Define the initial bankroll, unit, session stop loss, maximum loss per hand and optionally the first session hand ID.
5. Optionally configure OpenAI, then enable **AI automatica** once per browser session. New study decisions are sent automatically; repeated identical decisions use a cache.
6. Use **Laboratorio** for explicit simulated scenarios, or manually transcribe a saved screenshot and verify the fields.

In the card notation, `Ah Qh` means ace and queen of hearts. Suits: `c` clubs, `d` diamonds,
`h` hearts, `s` spades. Ten = `T`. The current pot **includes the opposing bet**.

## Optional OpenAI connection

Copy `.env.example` to `.env`, enter `OPENAI_API_KEY` and `OPENAI_MODEL`, and restart.
Choose a Responses API model available to your project with Structured Outputs; screenshot transcription also requires image input.
Environment variables work too and take precedence over `.env`.
The integration uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text)
with `store: false`. This setting is not a promise of zero provider retention.

Automatic calls require the session checkbox; manual explanations and screenshot transcription
have their own consent. Calls can incur charges on the configured OpenAI project. The API key
stays in the local Python process, never in browser storage. Decision requests send the current
cards, observed actions up to that point, budget constraints and profiles using aliases, **not
raw player names or future results**. Screenshot transcription sends the entire selected image:
crop/redact it yourself if needed. Images are not saved. All AI output needs human review.

`OPENAI_MAX_CALLS=20` caps attempted paid calls per server start across all AI features.
Set it to `0` to disable outgoing calls. The counter resets at restart, is not a currency spending
limit, and cannot account for other apps using the same key. Token usage and attempt counts
are returned by `/api/status`. There are no automatic retries. Timeout is 50 seconds.

OpenAI output explains assumptions and missing information. It does not invent a GTO solution.
No API key is needed for imports, replay, profiles, budget checks or the local teaching baseline.

## Default decision style

**Decisive, concise, and focused on positive expected value within loss limits.** The interface
leads with one proposed action, amount and a short rationale. A fold is a valid decisive action;
the objective does not mean mandatory aggression, chasing losses or guaranteed profit.

The model can select only a server-generated, budget-compliant candidate ID or abstain.
Amounts are filled in by the server, never trusted from generated text. Illegal, malformed,
refused, incomplete or unavailable model output leaves the explicitly labeled local reference
visible. Local reference confidence is always low: it is a teaching heuristic, not a trained policy.

Prompts are versioned in `prompts/decision_system.txt`, `prompts/explanation_system.txt` and
`prompts/vision_system.txt`. The API contract is documented in [docs/API.md](docs/API.md).

## What this version does

- Imports completed English PokerStars No Limit Hold’em histories (2–9 seats; up to 200 hands / 2 MB).
- Reconstructs contributions, stacks, folds, streets, returned bets and awards using integer money units.
- Checks the reconstructed pot against the summary, and awards plus rake against the pot.
- Replays actions without revealing future board cards or opponents’ showdown cards early.
- Evaluates best five-card hands from seven cards, including ace-low straights and split pots.
- Estimates showdown equity against uniformly random legal hands, or a specified heads-up holding.
- Shows a Monte Carlo sampling margin and the **conditional** call threshold/EV.
- Optionally transcribes an archived screenshot, then requires manual inspection before calculation.
- Derives check/fold/call/bet/raise/all-in availability, full-raise bounds and reopening rights from the action sequence.
- Applies stack, remaining bankroll, session stop loss and per-hand loss limits before an AI request.
- Persists imported hands and budget in `private/riverlens.sqlite3` using Python's built-in SQLite.
- Computes observed VPIP, PFR, postflop aggression and fold-to-bet frequencies from earlier completed hands only.

## Budget and opponent evidence

The initial bankroll is the amount **before the first included hand**. The start-hand field
defaults to `0` (all imported hands). Session profit/loss includes only earlier completed hands
with the same Hero and unit and IDs at or after the start ID. Never mix currencies: no conversion
is performed. No account connection exists, so deposits, withdrawals, missing hands and other
sessions are not discovered automatically.

Additional commitment is limited to the minimum of remaining stack, bankroll minus chips
already committed in this hand, remaining stop-loss room including current-hand commitment,
and remaining per-hand loss allowance. These are local study constraints, not controls on a real
gambling account. Demo limits are fictional and separate. Simulations use the configured budget
or a clearly marked fictional default, with zero assumed prior session profit/loss.

Profiles describe **observed actions**, never a person's real-world personality. VPIP/PFR use
hands dealt as the denominator. Postflop aggression frequency is (bets + raises)/(bets + raises
+ calls + folds), excluding checks; aggression factor is (bets + raises)/calls when calls exist.
Fold-to-bet uses only observed facing-bet opportunities. Fewer than 20 hands produces no style
classification; 20–99 is a limited sample. Illustrative style thresholds are not calibrated
opponent ranges. Missing opportunities are unknown, not a zero rate. Historical ordering uses
numeric PokerStars hand IDs; profiles are separated by unit, but not by stakes/table format.

## Scope and limitations

This is an offline learning MVP. There is no live screen capture, folder watcher, PokerStars
integration, overlay, input automation or betting execution. Screenshot reading is manual,
not a two-second live loop. It does not connect to a ChatGPT browser session.

The hand importer is deliberately conservative. It accepts a common English format;
localized formats, Omaha, run-it-twice, bomb pots, cash-out and special blind structures are
unsupported. Unknown actions, inconsistent pots and incomplete histories are rejected.
Only synthetic fixtures have been used so far; your exported file may reveal a format variant.

Random opponents are **not a betting range**. The engine does not infer ranges from action,
model folds, perform CFR/GTO solving, calculate ICM, or choose optimal fold/call/raise sizes.
Equity assumes all remaining board cards are dealt and all modeled opponents reach showdown.
Call EV = equity × (current pot + call) − call, valid only with no further betting, no side pot
and no rake. Multiway all-ins / partial calls disable this simplified calculation in replay.
The laboratory assumes one shared pot and equal eligibility; it uses explicit remaining stack,
street contribution, total hand contribution and full-raise increment. Side-pot equity is not modeled.
Incomplete opening bets, partial calls and multiway all-ins cause the decision layer to abstain.
The legal action list contains amount intervals; model candidates sample common sizes rather
than every possible cent. This is no-limit Hold'em only, not a generic poker rules engine.

The local baseline checks when free. Facing a bet it compares reference equity minus sampling
margin against pot odds plus an illustrative eight-percentage-point margin; an observed
selective aggressor with at least 20 hands adds seven points. It never selects a bet/raise itself;
AI can consider all supported candidates. These thresholds are intentionally exposed, **not
validated for profit**, and random-hand equity still does not establish profitable calls.
The reported 95% margin is a normal approximation for Monte Carlo sampling error only,
not a confidence bound on a strategy or profit. A fixed seed makes repeated scenarios reproducible.

Imported hands and limits survive restarts in the local SQLite archive; screenshots and AI consent
remain in browser memory. Keep original histories as backup. The server binds only to loopback,
checks Host and a per-process request token, and serves a fixed asset allowlist.
Do not expose this development server to a public network.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 server.py --no-browser
```

- `engine.py`: card evaluator, Monte Carlo equity, input validation and pot math.
- `history.py`: completed-hand parser and replay snapshots.
- `decisions.py`: legal moves, commitment limits and transparent local baseline.
- `archive.py`, `profiles.py`, `study.py`: persistence, earlier-hand evidence and validated model proposals.
- `server.py`: local HTTP API, explicit OpenAI calls and fixed static assets.
- `static/`: accessible browser UI, plain HTML/CSS/JavaScript.
- `examples/demo.txt`: two synthetic hands, including an uncalled returned bet.
- `tests/test_core.py`: evaluator cross-checks, split pots, parser conservation and mocked AI boundaries.

No dependencies are pinned because there are no third-party runtime dependencies.
Never commit `.env`, real hand histories or screenshots. Use `private/` for local sensitive files.

## Project status

Version 0.2.0 · local prototype. 43 tests pass, including rule bounds, loss limits, chronology,
archive deduplication, changed-budget cache invalidation, simultaneous-request deduplication and HTTP flows.
OpenAI request construction and response handling have mock coverage; a live API request
requires a configured project and has not been validated as part of this initial build.
JavaScript syntax is checked. A full browser/visual test could not be run in the build environment
because a browser binary was unavailable. Please treat the interface as a first prototype.
