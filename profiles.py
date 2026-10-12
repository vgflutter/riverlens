"""Observed poker statistics; never infer real-world personality traits."""


def profiles(hands, names):
    result = []
    for name in names:
        n = vpip = pfr = bets = raises = calls = folds = checks = faced = folded_to_bet = 0
        for hand in hands:
            seats = hand['snapshots'][0]['players']
            player = next((p for p in seats if p['name'] == name and not p['folded']), None)
            if player is None:
                continue
            n += 1
            voluntary = preflop_raise = False
            for i, snap in enumerate(hand['snapshots']):
                if snap['actor'] != name or snap['kind'] != 'action':
                    continue
                action = snap.get('action')
                if snap['street'] == 'PREFLOP':
                    voluntary |= action in ('call', 'bet', 'raise')
                    preflop_raise |= action == 'raise'
                else:
                    bets += action == 'bet'
                    raises += action == 'raise'
                    calls += action == 'call'
                    folds += action == 'fold'
                    checks += action == 'check'
                    previous = hand['snapshots'][i-1]
                    p = next(p for p in previous['players'] if p['name'] == name)
                    if previous['current_bet'] > p['bet']:
                        faced += 1
                        folded_to_bet += action == 'fold'
            vpip += voluntary
            pfr += preflop_raise
        aggression_actions = bets + raises + calls + folds
        vp = vpip/n if n else None
        pr = pfr/n if n else None
        afq = (bets+raises)/aggression_actions if aggression_actions else None
        if n < 20:
            style = 'Campione insufficiente'
        else:
            participation = 'Selettivo' if vp < .22 else 'Ampio' if vp > .35 else 'Intermedio'
            aggression = 'aggressività alta' if afq is not None and afq >= .5 else 'aggressività bassa' if afq is not None and afq < .3 else 'aggressività incerta'
            style = participation + ' · ' + aggression
        result.append({'name': name, 'hands': n, 'vpip': vp, 'pfr': pr,
                       'aggression_frequency': afq,
                       'aggression_factor': (bets+raises)/calls if calls else None,
                       'fold_to_bet': folded_to_bet/faced if faced else None,
                       'fold_opportunities': faced, 'postflop_actions': aggression_actions + checks,
                       'style': style, 'sample_quality': 'insufficient' if n < 20 else 'limited' if n < 100 else 'descriptive',
                       'note': 'Frequenze osservate, non personalità o range certo. Stime dipendenti da formato e campione.'})
    return result
