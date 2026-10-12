"""Small, dependency-free Hold'em evaluator. Values are study estimates, not a solver."""
from collections import Counter
import math
import random
import re
import time

RANKS = '23456789TJQKA'
SUITS = 'cdhs'
DECK = [r + s for r in RANKS for s in SUITS]
LABELS = ['Carta alta', 'Coppia', 'Doppia coppia', 'Tris', 'Scala', 'Colore',
          'Full', 'Poker', 'Scala colore']


def cards(value):
    if isinstance(value, str):
        value = value.replace('10', 'T')
        for a, b in zip('♣♦♥♠', SUITS):
            value = value.replace(a, b)
        value = re.split(r'[\s,]+', value.strip()) if value.strip() else []
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError('Le carte devono essere una lista o testo: Ah Kd.')
    normalized = [x[0].upper() + x[1].lower() if len(x) == 2 else x for x in value]
    if any(x not in DECK for x in normalized):
        raise ValueError('Carta non valida. Usa Ah Kd, oppure A♥ K♦.')
    if len(normalized) != len(set(normalized)):
        raise ValueError('Una carta compare più volte.')
    return normalized


def straight(ranks):
    values = set(ranks)
    if 14 in values:
        values.add(1)
    for high in range(14, 5 - 1, -1):
        if all(n in values for n in range(high - 4, high + 1)):
            return high
    return 0


def evaluate(hand):
    """Comparable tuples; choose the best five from 5–7 cards without enumeration."""
    counts = Counter(RANKS.index(c[0]) + 2 for c in hand)
    ranks = sorted(counts, reverse=True)
    flush = next(([RANKS.index(c[0]) + 2 for c in hand if c[1] == s]
                  for s in SUITS if sum(c[1] == s for c in hand) >= 5), [])
    if flush and straight(flush):
        return (8, straight(flush))
    quads = [r for r in ranks if counts[r] == 4]
    if quads:
        return (7, quads[0], next(r for r in ranks if r != quads[0]))
    trips = [r for r in ranks if counts[r] >= 3]
    if trips:
        pairs = [r for r in ranks if r != trips[0] and counts[r] >= 2]
        if pairs:
            return (6, trips[0], pairs[0])
    if flush:
        return (5, *sorted(flush, reverse=True)[:5])
    if straight(ranks):
        return (4, straight(ranks))
    if trips:
        return (3, trips[0], *[r for r in ranks if r != trips[0]][:2])
    pairs = [r for r in ranks if counts[r] >= 2]
    if len(pairs) >= 2:
        return (2, *pairs[:2], next(r for r in ranks if r not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *[r for r in ranks if r != pairs[0]][:3])
    return (0, *ranks[:5])


def number(value, label, maximum=1e9):
    if isinstance(value, bool):
        raise ValueError(f'{label}: valore non valido.')
    try:
        v = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{label}: inserisci un numero.') from None
    if not math.isfinite(v) or not 0 <= v <= maximum:
        raise ValueError(f'{label}: valore fuori intervallo.')
    return v


def analyze(data):
    started = time.perf_counter()
    hero, board, villain = (cards(data.get(k, [])) for k in ('hero', 'board', 'villain'))
    if len(hero) != 2 or len(board) not in (0, 3, 4, 5) or len(villain) not in (0, 2):
        raise ValueError('Servono 2 carte personali, 0/3/4/5 carte comuni e 0/2 carte avversarie.')
    all_cards = hero + board + villain
    if len(set(all_cards)) != len(all_cards):
        raise ValueError('La stessa carta compare in posizioni diverse.')
    count = number(data.get('opponents', 1), 'Avversari', 8)
    iterations = number(data.get('iterations', 2500), 'Simulazioni', 10000)
    if not count.is_integer() or count < 1 or not iterations.is_integer() or iterations < 100:
        raise ValueError('Usa 1–8 avversari e 100–10000 simulazioni intere.')
    count, iterations = int(count), int(iterations)
    if villain and count != 1:
        raise ValueError('Le carte avversarie note sono supportate solo contro un avversario.')
    pot = number(data.get('pot', 0), 'Piatto')
    call = number(data.get('call', 0), 'Da chiamare')
    if call > pot:
        raise ValueError('Il piatto deve includere la puntata avversaria: non può essere inferiore al call.')
    if data.get('complex_pot'):
        raise ValueError('Questo punto contiene possibili side pot o un call parziale: il replay è disponibile, il calcolo semplificato no.')
    deck = [c for c in DECK if c not in all_cards]
    rng = random.Random(42)
    shares, squares, wins, ties = 0.0, 0.0, 0, 0
    exact = len(board) == 5 and bool(villain)
    n = 1 if exact else iterations
    for _ in range(n):
        draw = rng.sample(deck, 5 - len(board) + (0 if villain else 2 * count))
        runout = board + draw[:5 - len(board)]
        rest = draw[5 - len(board):]
        opponents = [villain] if villain else [rest[i*2:i*2+2] for i in range(count)]
        scores = [evaluate(hero + runout)] + [evaluate(h + runout) for h in opponents]
        best = max(scores)
        winners = sum(s == best for s in scores)
        share = 1 / winners if scores[0] == best else 0
        shares += share
        squares += share * share
        wins += share == 1
        ties += 0 < share < 1
    equity = shares / n
    margin = 0 if exact else 1.96 * math.sqrt(max(0, squares/n - equity**2) / n)
    threshold = call / (pot + call) if call else 0
    made = LABELS[evaluate(hero + board)[0]] if board else ('Coppia in mano' if hero[0][0] == hero[1][0] else 'Carte non accoppiate')
    context = {'hero': hero, 'board': board, 'villain': villain, 'opponents': count, 'pot': pot, 'call': call}
    return {'equity': equity, 'margin95': margin, 'win': wins/n, 'tie': ties/n,
            'pot_odds': threshold, 'call_ev_model': equity * (pot + call) - call,
            'made_hand': made, 'samples': n, 'exact': exact,
            'elapsed_ms': round((time.perf_counter() - started) * 1000), 'context': context,
            'assumption': 'Mano avversaria specificata' if villain else 'Mani avversarie uniformemente casuali, senza range dedotto dalle azioni',
            'limitations': 'Equity allo showdown. EV del call solo se non ci sono altre puntate, side pot o rake. Non è una soluzione GTO né una raccomandazione automatica.'}
