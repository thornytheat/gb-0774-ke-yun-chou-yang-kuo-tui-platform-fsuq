from collections import defaultdict
from fractions import Fraction

from app.config import CHOICES
from app.modules.weights.calibration import calibrate_layer

# Manual-review reasons, ordered by how decisive they are.
SEVERITY = ('SCHED_FLIP', 'SCHED_TIE', 'SCHED_NO_SAMPLE')


def projected(population, answers):
    """Exact extrapolated people per choice for one layer.

    ``population`` keeps the layer's full ticket count, so removing one shift's
    answers never removes that shift's tickets from the calibration base.
    """
    layer = calibrate_layer(population, answers)
    if layer['status'] == 'zero_response':
        return None
    return {key: Fraction(value) for key, value in layer['quotas'].items()}


def top_choice(shares):
    """Strictly greatest exact share is the pick; equal maxima are a tie."""
    if shares is None or not any(value > 0 for value in shares.values()):
        return None, 'SCHED_NO_SAMPLE', []
    best = max(shares.values())
    heads = [key for key in CHOICES if shares[key] == best]
    if len(heads) > 1:
        return None, 'SCHED_TIE', heads
    return heads[0], None, heads


def trip_owners(frame):
    """Shifts attached to each layer, taken from the latest frame revision."""
    owners = defaultdict(list)
    for trip, history in frame['trips'].items():
        owners[history[-1]['layer']].append(trip)
    return owners


def assess(integers, population, answers, trips):
    shares = projected(population, answers)
    preferred, problem, leaders = top_choice(shares)
    simulations = []
    if problem is None:
        for trip in trips:
            reduced = [answer for answer in answers if answer['trip'] != trip]
            choice, issue, _ = top_choice(projected(population, reduced))
            if issue is None and choice == preferred:
                continue
            # A shift removal that also empties the sample still overturns the
            # pick we have: the layer is no longer safe to schedule, so it
            # counts as a flip rather than a baseline no-sample.
            reason = 'SCHED_TIE' if issue == 'SCHED_TIE' else 'SCHED_FLIP'
            simulations.append(dict(trip=trip, choice=choice, reason=reason))
        for code in SEVERITY:
            if any(test['reason'] == code for test in simulations):
                problem = code
                break
    return dict(population=population, preferred=preferred,
                share=str(shares[preferred]) if shares and preferred else None,
                shares={key: str(value) for key, value in shares.items()} if shares else None,
                leaders=leaders, count=integers[preferred] if preferred else None,
                integers=integers, status='manual' if problem else 'scheduled',
                reason=problem, trips=len(trips), tests=simulations)


def advise(frame, allocation):
    answers, owners = defaultdict(list), trip_owners(frame)
    for row in frame['valid']:
        answers[row['layer']].append(row)
    layers = {name: assess(row['integers'], row['population'], answers[name], owners[name])
              for name, row in allocation['layers'].items()}
    scheduled = [name for name, entry in layers.items() if entry['status'] == 'scheduled']
    manual = [name for name, entry in layers.items() if entry['status'] == 'manual']
    return dict(layers=layers, scheduled=scheduled, manual=manual, ready=not manual,
                advice={name: layers[name]['preferred'] for name in scheduled})
