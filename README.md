# RiverLens

**Offline poker hand replay, equity analysis and AI-powered review.**

A small local study room for completed Texas Hold’em hands and simulated scenarios.
Python 3.10+ and a browser. No pip, npm, database or build step required.
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

1. The app loads two **synthetic example hands**. No real players or money are involved.
2. Select a hand, move through its actions, and press **Calcola equity** at a Hero decision.
3. Use **Laboratorio** to change cards, pot, call amount and opponents, or select a preset.
4. Import a completed English PokerStars `.txt` hand history. Multiple hands are supported.
5. Optionally connect an OpenAI project for explanations and manual transcription of an archived screenshot.

In the card notation, `Ah Qh` means ace and queen of hearts. Suits: `c` clubs, `d` diamonds,
`h` hearts, `s` spades. Ten = `T`. The current pot **includes the opposing bet**.

## Optional OpenAI connection

Copy `.env.example` to `.env`, enter `OPENAI_API_KEY` and `OPENAI_MODEL`, and restart.
Choose a Responses API model available to your project; screenshot transcription requires image input.
Environment variables work too and take precedence over `.env`.
The integration uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text)
with `store: false`. This setting is not a promise of zero provider retention.

API calls happen only after a manual button press and explicit consent. They can incur charges
on the configured OpenAI project. The API key stays in the local Python process, never in browser
storage. Explanations send the current cards and mathematical results, **not the hand-history
file or player names**. Screenshot transcription sends the full selected image: crop/redact it
yourself if needed. Images are not saved by RiverLens. All AI output needs human review.

OpenAI output explains assumptions and missing information. It does not invent a GTO solution.
No API key is needed for imports, replay or equity calculations.

## What this version does

- Imports completed English PokerStars No Limit Hold’em histories (2–9 seats; up to 200 hands / 2 MB).
- Reconstructs contributions, stacks, folds, streets, returned bets and awards using integer money units.
- Checks the reconstructed pot against the summary, and awards plus rake against the pot.
- Replays actions without revealing future board cards or opponents’ showdown cards early.
- Evaluates best five-card hands from seven cards, including ace-low straights and split pots.
- Estimates showdown equity against uniformly random legal hands, or a specified heads-up holding.
- Shows a Monte Carlo sampling margin and the **conditional** call threshold/EV.
- Optionally transcribes an archived screenshot, then requires manual inspection before calculation.

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
The laboratory assumes one shared pot and equal eligibility; no stacks or side pots are modeled.
The reported 95% margin is a normal approximation for Monte Carlo sampling error only,
not a confidence bound on a strategy or profit. A fixed seed makes repeated scenarios reproducible.

The app holds imported hands and images in browser memory. Reloading resets the session;
keep original files if you want to review them later. The server binds only to loopback,
checks Host and a per-process request token, and serves a fixed asset allowlist.
Do not expose this development server to a public network.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 server.py --no-browser
```

- `engine.py`: card evaluator, Monte Carlo equity, input validation and pot math.
- `history.py`: completed-hand parser and replay snapshots.
- `server.py`: local HTTP API, explicit OpenAI calls and fixed static assets.
- `static/`: accessible browser UI, plain HTML/CSS/JavaScript.
- `examples/demo.txt`: two synthetic hands, including an uncalled returned bet.
- `tests/test_core.py`: evaluator cross-checks, split pots, parser conservation and mocked AI boundaries.

No dependencies are pinned because there are no third-party runtime dependencies.
Never commit `.env`, real hand histories or screenshots. Use `private/` for local sensitive files.

## Project status

Version 0.1.0 · local prototype. The local engine, HTTP API and replay data are tested without credentials.
OpenAI request construction and response handling have mock coverage; a live API request
requires a configured project and has not been validated as part of this initial build.
JavaScript syntax is checked. A full browser/visual test could not be run in the build environment
because a browser binary was unavailable. Please treat the interface as a first prototype.
