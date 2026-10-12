"""Conservative importer for completed English PokerStars NL Hold'em histories."""
import copy
from decimal import Decimal, InvalidOperation
import re
from engine import cards

MONEY = r'[$€£]?([\d,]+(?:\.\d+)?)'


def amount(value):
    try:
        scaled = Decimal(value.replace(',', '')) * 100
        if scaled != scaled.to_integral_value():
            raise ValueError('Sono supportati importi con al massimo due decimali.')
        return int(scaled)
    except InvalidOperation:
        raise ValueError('Importo non valido nella hand history.') from None


def parse_one(text):
    lines = [l.strip('\ufeff\r ') for l in text.strip().splitlines() if l.strip()]
    if not lines or not re.search(r"Hold'em No Limit", lines[0]):
        raise ValueError("Formato supportato: PokerStars, Hold'em No Limit, testo inglese.")
    if '*** SUMMARY ***' not in lines:
        raise ValueError('Mano incompleta: manca SUMMARY. Importa una mano conclusa.')
    if re.search(r'FIRST|SECOND|THIRD|Run It Twice|Bomb Pot|CASH OUT|cashed out|posts.*(?:dead|both)', text, re.I):
        raise ValueError('Variante non supportata (run-it-twice, cash-out, bomb pot o bui speciali).')
    ident = re.search(r'#(\d+)', lines[0])
    if not ident:
        raise ValueError('Identificativo mano mancante.')
    header, body = text.split('*** SUMMARY ***', 1)
    total = re.search(r'Total pot ' + MONEY, body)
    if not total:
        raise ValueError('Riepilogo incompleto: manca Total pot.')
    players = {}
    for match in re.finditer(r'^Seat (\d+): (.+) \(' + MONEY + r' in chips\)([^\r\n]*)', header, re.M):
        seat, name, stack, suffix = match.groups()
        players[name] = {'name': name, 'seat': int(seat), 'stack': amount(stack),
                         'bet': 0, 'invested': 0, 'folded': 'is sitting out' in suffix, 'shown': []}
    if not 2 <= len(players) <= 9:
        raise ValueError('Servono da 2 a 9 posti con stack riconoscibili.')
    hero_match = re.search(r'^Dealt to (.+) \[([^\]]+)\]', header, re.M)
    if not hero_match or hero_match[1] not in players:
        raise ValueError('Mancano le tue carte (Dealt to).')
    hero, hole = hero_match[1], cards(hero_match[2])
    if len(hole) != 2:
        raise ValueError('Carte personali non valide.')
    button_match = re.search(r'Seat #(\d+) is the button', header)
    button = int(button_match[1]) if button_match else None
    snapshots, board, pot, street = [], [], 0, 'PREFLOP'
    awarded = 0

    def snapshot(label, actor=None, kind='event'):
        live = [p for p in players.values() if not p['folded']]
        target = max((p['bet'] for p in live), default=0)
        due = max(0, target - players[hero]['bet']) if not players[hero]['folded'] else 0
        complex_pot = (due > players[hero]['stack'] or
                       (len(live) > 2 and any(p['stack'] == 0 for p in live)))
        snapshots.append({'label': label, 'actor': actor, 'kind': kind, 'street': street,
                          'players': copy.deepcopy(list(players.values())), 'board': list(board),
                          'hero': hero, 'hole': list(hole), 'pot': pot/100, 'call': due/100,
                          'opponents': len([p for p in live if p['name'] != hero]),
                          'complex_pot': complex_pot})

    def invest(name, value, is_ante=False):
        nonlocal pot
        if value < 0 or value > players[name]['stack']:
            raise ValueError(f'Puntata incompatibile con lo stack di {name}.')
        players[name]['stack'] -= value
        players[name]['invested'] += value
        if not is_ante:
            players[name]['bet'] += value
        pot += value

    snapshot('Inizio della mano')
    for line in header.splitlines():
        line = line.strip()
        street_match = re.match(r'\*\*\* (FLOP|TURN|RIVER) \*\*\*', line)
        if street_match:
            next_street = street_match[1]
            next_board = cards(' '.join(re.findall(r'\[([^\]]+)\]', line)))
            expected = {'FLOP': 3, 'TURN': 4, 'RIVER': 5}[next_street]
            if (len(next_board) != expected or len(set(next_board + hole)) != expected + 2
                    or next_board[:len(board)] != board
                    or {'PREFLOP':'FLOP', 'FLOP':'TURN', 'TURN':'RIVER'}.get(street) != next_street):
                raise ValueError('Carte comuni incoerenti o duplicate.')
            street, board = next_street, next_board
            for p in players.values():
                p['bet'] = 0
            snapshot(street.title())
            continue
        if line == '*** SHOW DOWN ***':
            snapshot('Showdown', kind='showdown')
            continue
        returned = re.match(r'Uncalled bet \(' + MONEY + r'\) returned to (.+)', line)
        if returned:
            value, name = amount(returned[1]), returned[2]
            if name not in players or value > players[name]['bet']:
                raise ValueError('Restituzione di puntata incoerente.')
            players[name]['stack'] += value
            players[name]['bet'] -= value
            players[name]['invested'] -= value
            pot -= value
            snapshot(line, name)
            continue
        collected = re.match(r'(.+) collected ' + MONEY + r' from (?:main |side )?pot', line)
        if collected:
            name, value = collected[1], amount(collected[2])
            if name not in players:
                raise ValueError('Vincitore non riconosciuto.')
            players[name]['stack'] += value
            awarded += value
            snapshot(line, name, 'award')
            continue
        name = next((n for n in sorted(players, key=len, reverse=True) if line.startswith(n + ': ')), None)
        if name is None:
            continue
        action = line[len(name) + 2:]
        post = re.match(r'posts (small blind|big blind|the ante) ' + MONEY + r'(?: and is all-in)?$', action)
        betting = re.match(r'(calls|bets) ' + MONEY + r'(?: and is all-in)?$', action)
        raising = re.match(r'raises ' + MONEY + r' to ' + MONEY + r'(?: and is all-in)?$', action)
        if post:
            invest(name, amount(post[2]), post[1] == 'the ante')
            kind = 'blind'
        elif betting:
            invest(name, amount(betting[2]))
            kind = 'action'
        elif raising:
            invest(name, amount(raising[2]) - players[name]['bet'])
            kind = 'action'
        elif action.startswith('folds'):
            players[name]['folded'] = True
            kind = 'action'
        elif action == 'checks':
            kind = 'action'
        elif action.startswith('shows ['):
            shown = cards(re.search(r'\[([^\]]+)\]', action)[1])
            if len(shown) != 2 or (name == hero and set(shown) != set(hole)):
                raise ValueError('Carte mostrate incoerenti.')
            occupied = hole + board + [c for p in players.values() if p['name'] not in (hero, name) for c in p['shown']]
            if name != hero and set(shown) & set(occupied):
                raise ValueError('Carta mostrata già presente sul tavolo.')
            players[name]['shown'] = shown
            kind = 'reveal'
        elif action.startswith(('mucks hand', "doesn't show hand", 'sits out', 'is sitting out', 'has timed out', 'is disconnected', 'is connected', 'said,', 'leaves the table')):
            continue
        else:
            raise ValueError(f'Azione non supportata: {action[:100]}')
        snapshot(line, name, kind)
    if pot != amount(total[1]):
        raise ValueError(f'Il piatto ricostruito ({pot/100:g}) non coincide con Total pot ({amount(total[1])/100:g}).')
    rake_match = re.search(r'Rake ' + MONEY, body)
    rake = amount(rake_match[1]) if rake_match else 0
    if awarded + rake != pot:
        raise ValueError('Riepilogo incompleto o non supportato: vincite e rake non coincidono con il piatto.')
    for i, snap in enumerate(snapshots):
        nxt = snapshots[i+1] if i+1 < len(snapshots) else None
        snap['next_actor'] = nxt['actor'] if nxt and nxt['kind'] == 'action' else None
        snap['hero_decision'] = snap['next_actor'] == hero
        for p in snap['players']:
            for key in ('stack', 'bet', 'invested'):
                p[key] /= 100
    return {'id': ident[1], 'title': lines[0], 'hero': hero, 'hole': hole,
            'button': button, 'snapshots': snapshots, 'total_pot': pot/100, 'completed': True}


def parse_histories(text):
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise ValueError('Importa un file di testo fino a 2 MB.')
    blocks = re.split(r'(?=^PokerStars (?:Hand|Game) #)', text.lstrip('\ufeff'), flags=re.M)
    blocks = [b.strip() for b in blocks if b.strip()]
    if len(blocks) > 200:
        raise ValueError('Importa al massimo 200 mani per volta.')
    results, errors = [], []
    for i, block in enumerate(blocks):
        try:
            results.append(parse_one(block))
        except ValueError as error:
            errors.append({'hand': i + 1, 'message': str(error)})
    if not results:
        raise ValueError(errors[0]['message'] if errors else 'Nessuna mano trovata.')
    return {'hands': results, 'errors': errors}
