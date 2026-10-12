"""Legal action bounds, user-defined loss limits, and transparent study baseline."""
from decimal import Decimal
from engine import analyze, number


def cents(value, label='Importo'):
    n = number(value, label)
    scaled = Decimal(str(n)) * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(f'{label}: usa al massimo due decimali.')
    return int(scaled)


def legal_actions(state):
    stack = cents(state['stack'], 'Stack')
    hero_bet = cents(state.get('hero_bet', 0))
    current = cents(state.get('current_bet', 0))
    blind = cents(state['big_blind'], 'Big blind')
    increment = cents(state.get('min_raise_increment', state['big_blind']))
    if blind <= 0 or increment < blind or current < hero_bet:
        raise ValueError('Big blind, puntata o incremento di rilancio incoerenti.')
    if not state.get('hero_turn', True) or state.get('folded') or stack == 0:
        return []
    due = current - hero_bet
    actions = []

    def add(kind, low, high=None, all_in=False, underlying=None):
        actions.append({'action': kind, 'min_add': low/100, 'max_add': (low if high is None else high)/100,
                        'all_in': all_in, 'underlying': underlying or kind, 'hero_bet': hero_bet/100})

    if due:
        add('fold', 0)
        paid = min(due, stack)
        add('all_in' if paid == stack else 'call', paid, all_in=paid == stack, underlying='call')
    else:
        add('check', 0)
    can_raise = state.get('raise_reopened', True) is True and state.get('opponent_can_respond', True) is True
    # An incomplete opening bet is deliberately excluded until room-specific rules are supported.
    if stack > due and can_raise and not state.get('ambiguous_bet'):
        minimum = blind if current == 0 else current + increment - hero_bet
        kind = 'bet' if current == 0 else 'raise'
        if minimum < stack:
            add(kind, minimum, stack-1)
        add('all_in', stack, all_in=True, underlying=kind)
    return actions


def budget_status(config, state, net=0):
    if config is None:
        return {'configured': False, 'allowed_add': 0, 'blocked': True,
                'reason': 'Imposta il budget prima delle proposte sulle mani importate.'}
    if config['unit'] != state['unit']:
        return {'configured': True, 'unit': config['unit'], 'allowed_add': 0, 'blocked': True,
                'reason': 'Unità del budget diversa da quella del tavolo. Nessuna conversione automatica.'}
    invested = cents(state.get('invested', 0))
    balance = cents(config['bankroll']) + round(net*100)
    loss_room = cents(config['stop_loss']) + round(net*100) - invested
    hand_room = cents(config['max_hand_loss']) - invested
    allowed = max(0, min(cents(state['stack']), balance-invested, loss_room, hand_room))
    return {'configured': True, 'demo': config.get('demo', False), 'unit': config['unit'],
            'bankroll_before_hand': balance/100, 'net_before_hand': net, 'committed_this_hand': invested/100,
            'stop_loss': config['stop_loss'], 'max_hand_loss': config['max_hand_loss'],
            'allowed_add': allowed/100, 'blocked': allowed == 0,
            'stop_session': balance <= 0 or cents(config['stop_loss']) + round(net*100) <= 0,
            'reason': 'Limite di perdita raggiunto: nessuna ulteriore chip da impegnare.' if not allowed else 'Limiti simulati rispettati; non controllano un conto di gioco reale.'}


def candidates(actions, state, budget):
    """Candidate sizing grid. Legal action intervals are returned separately, in full."""
    pot, due = cents(state['pot']), max(0, cents(state['current_bet'])-cents(state.get('hero_bet',0)))
    cap = cents(budget['allowed_add'])
    result = []
    for action in actions:
        low, high = cents(action['min_add']), min(cents(action['max_add']), cap)
        if low > high:
            continue
        values = {low}
        if action['action'] in ('bet','raise'):
            values |= {max(low,min(high,due+round(fraction*(pot+due)))) for fraction in (.33,.5,.75,1)}
        for value in sorted(values):
            result.append({'id': f"{action['action']}:{value}", 'action': action['action'],
                           'amount_add': value/100, 'raise_to': (value+cents(state.get('hero_bet',0)))/100 if action['underlying'] in ('raise','bet') else None,
                           'all_in': action['all_in'], 'underlying': action['underlying']})
    return result


def baseline(state, options, equity, opponent_profiles, blocked_reason=None):
    reason = blocked_reason
    if not options or reason:
        return {'candidate_id': None, 'confidence': 'low', 'rationale': reason or 'Nessuna azione da valutare in questo punto.', 'assumptions': [], 'source': 'local'}
    check = next((x for x in options if x['action']=='check'), None)
    fold = next((x for x in options if x['action']=='fold'), None)
    call = next((x for x in options if x['underlying']=='call'), None)
    if check:
        selected, reason = check, 'Linea di riferimento passiva: check senza impegnare altre chip. Non valuta il valore di una puntata.'
    elif not call:
        selected, reason = fold, 'Il call supera il limite di perdita configurato.'
    elif equity is None:
        return {'candidate_id': None, 'confidence': 'low', 'rationale': 'Side pot o stato ambiguo: revisione manuale richiesta.', 'assumptions': [], 'source': 'local'}
    else:
        # Conservative demonstration only, not calibrated to player ranges or expected profit.
        margin = .08
        tight_aggressors = [p for p in opponent_profiles if p['name']==state.get('last_aggressor') and p['hands']>=20 and p['vpip'] is not None and p['vpip']<.22]
        if tight_aggressors:
            margin += .07
        choose_call = equity['equity'] - equity['margin95'] > equity['pot_odds'] + margin
        selected = call if choose_call else fold
        reason = ('Call come ipotesi di studio: equity del modello sopra la soglia prudenziale.' if choose_call else 'Fold come ipotesi di studio: equity del modello insufficiente rispetto alla soglia prudenziale.')
        if tight_aggressors:
            reason += ' Margine aumentato perché l’aggressore osservato partecipa a poche mani.'
    return {'candidate_id': selected['id'] if selected else None, 'confidence': 'low', 'rationale': reason,
            'assumptions': ['Regola didattica non ottimizzata; gli avversari casuali non sono un range di puntata.',
                            'Puntate future, fold equity e ICM non modellati. Nessuna garanzia di profitto.'], 'source': 'local'}


def assess(state, config, net, opponent_profiles):
    actions = legal_actions(state)
    budget = budget_status(config, state, net)
    choices = candidates(actions, state, budget)
    blocked = None
    if not budget['configured'] or budget.get('unit', state['unit']) != state['unit']:
        blocked = budget['reason']
    if state.get('complex_pot') or state.get('ambiguous_bet'):
        blocked = 'Side pot, call parziale o puntata incompleta: stato non supportato dal modello di studio.'
    equity = None
    if actions and not blocked:
        equity = analyze({'hero': state['hero'], 'board': state['board'], 'villain': state.get('villain',[]),
                          'opponents': state['opponents'], 'pot': state['pot'],
                          'call': max(0,state['current_bet']-state['hero_bet'])})
    recommendation = baseline(state, choices, equity, opponent_profiles, blocked)
    return {'legal_actions': actions, 'candidates': choices, 'budget': budget, 'equity': equity,
            'recommendation': recommendation, 'opponents': opponent_profiles, 'blocked': blocked}
