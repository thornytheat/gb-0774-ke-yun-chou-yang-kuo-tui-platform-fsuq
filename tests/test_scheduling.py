from app.modules.scheduling.service import advise, projected, top_choice
from app.pipeline import run
from app.source import sources


def build(rows, frame):
    header = 'ticket,trip,reported_layer,visit,status,probability,choice\n'
    text = header + ''.join(f'{ticket},{trip},{layer},1,YES,{prob},{choice}\n'
                            for ticket, trip, layer, prob, choice in rows)
    return run({'returns':text, 'frame':
                'trip,revision,layer,tickets\n'+''.join(f'{t},{r},{l},{n}\n' for t,r,l,n in frame)})


def test_steady_layer_is_scheduled():
    answer = build([('a1','t1','甲','1/2','direct'), ('a2','t1','甲','1/2','direct'),
                    ('a3','t1','甲','1/2','transfer'),
                    ('b1','t2','甲','1/2','direct'), ('b2','t2','甲','1/2','direct'),
                    ('b3','t2','甲','1/2','transfer'),
                    ('c1','t3','甲','1/2','direct'), ('c2','t3','甲','1/2','direct'),
                    ('c3','t3','甲','1/2','transfer')],
                   [('t1',1,'甲',100), ('t2',1,'甲',100), ('t3',1,'甲',100)])
    layer = answer['scheduling']['layers']['甲']
    assert layer['status'] == 'scheduled' and layer['preferred'] == 'direct'
    assert answer['scheduling']['advice'] == {'甲': 'direct'} and answer['scheduling']['ready']


def test_flip_goes_manual():
    answer = build([('a1','t1','乙','1/2','direct'), ('a2','t1','乙','1/2','direct'),
                    ('a3','t1','乙','1/2','direct'),
                    ('b1','t2','乙','1/2','direct'),
                    ('b2','t2','乙','1/2','other'), ('b3','t2','乙','1/2','other'),
                    ('b4','t2','乙','1/2','other')],
                   [('t1',1,'乙',100), ('t2',1,'乙',100)])
    layer = answer['scheduling']['layers']['乙']
    assert layer['status'] == 'manual' and layer['reason'] == 'SCHED_FLIP'
    assert layer['preferred'] == 'direct'
    assert [row['trip'] for row in layer['tests']] == ['t1']
    assert layer['tests'][0]['choice'] == 'other'


def test_tie_goes_manual():
    answer = build([('a1','t1','丙','1/2','direct'), ('a2','t1','丙','1/2','direct'),
                    ('b1','t2','丙','1/2','other'), ('b2','t2','丙','1/2','other')],
                   [('t1',1,'丙',100), ('t2',1,'丙',100)])
    layer = answer['scheduling']['layers']['丙']
    assert layer['status'] == 'manual' and layer['reason'] == 'SCHED_TIE'
    assert layer['preferred'] is None and set(layer['leaders']) == {'direct', 'other'}


def test_zero_response_layer_has_no_sample():
    answer = build([('a1','t1','丁','1/2','direct')],
                   [('t1',1,'丁',40), ('t2',1,'戊',60)])
    assert answer['scheduling']['layers']['戊']['reason'] == 'SCHED_NO_SAMPLE'
    assert answer['scheduling']['layers']['戊']['preferred'] is None


def test_tickets_stay_in_calibration_base(dirty):
    # Dropping a shift's answers must keep the layer's full ticket base: the
    # surviving answers still calibrate up to 100 people, not to fewer tickets.
    answers = [row for row in dirty['frame']['valid'] if row['layer'] == '城北']
    without_trip = [row for row in answers if row['trip'] != answers[0]['trip']]
    shares = projected(100, without_trip)
    assert sum(shares.values()) == 100
    # With no answers at all the base is still 100, surfacing no-sample, not zero people.
    assert projected(100, []) is None and top_choice(None)[1] == 'SCHED_NO_SAMPLE'


def test_dirty_layer_verdicts(dirty):
    scheduling = dirty['scheduling']
    assert scheduling['layers']['城北']['reason'] == 'SCHED_TIE'
    assert scheduling['layers']['末班']['reason'] == 'SCHED_NO_SAMPLE'
    assert scheduling['layers']['枢纽']['reason'] == 'SCHED_FLIP'
    assert scheduling['manual'] == ['城北', '末班', '枢纽'] and not scheduling['ready']


def test_advice_only_covers_scheduled_layers(dirty):
    scheduling = dirty['scheduling']
    assert all(name in scheduling['layers'] for name in scheduling['advice'])
    assert set(scheduling['advice']) == set(scheduling['scheduled'])


def test_archive_keeps_recorded_advice(client):
    key = client.post('/api/runs', json={'sample': 'dirty'}).json['id']
    record = client.get('/api/runs/' + key).json['scheduling']
    assert record['manual'] == ['城北', '末班', '枢纽']
    assert record['layers']['枢纽']['reason'] == 'SCHED_FLIP'


def test_late_visit_readjudicated_and_original_kept(client, tables):
    original = client.post('/api/runs', json={'sample': 'dirty'}).json
    assert original['scheduling']['layers']['枢纽']['reason'] == 'SCHED_FLIP'
    late = tables['returns'] + '00012,0002,枢纽,3,YES,1/4,transfer\n'
    updated = client.post('/api/runs', json={'tables': {**tables, 'returns': late}}).json
    assert updated['id'] != original['id']
    assert updated['scheduling']['layers']['枢纽']['preferred'] == 'transfer'
    assert updated['scheduling']['layers']['枢纽']['reason'] is None
    kept = client.get('/api/runs/' + original['id']).json
    assert kept['scheduling'] == original['scheduling']
    assert kept['manifest']['result_sha256'] == original['manifest']['result_sha256']


def test_rerun_reuses_original_record(client):
    first = client.post('/api/runs', json={'sample': 'dirty'}).json
    again = client.post(f"/api/runs/{first['id']}/rerun").json
    assert again['reused'] and again['id'] == first['id']
    assert again['scheduling'] == first['scheduling']
    assert len(client.get('/api/runs').json['runs']) == 1


def test_only_shift_removal_counts_as_flip():
    # 甲 has exactly one shift; removing its answers empties the sample, which
    # overturns the current pick and must go to manual review, not auto-schedule.
    answer = build([('a1','u1','甲','1/2','direct'), ('a2','u1','甲','1/2','direct'),
                    ('a3','u1','甲','1/2','transfer')],
                   [('u1',1,'甲',100)])
    layer = answer['scheduling']['layers']['甲']
    assert layer['status'] == 'manual' and layer['reason'] == 'SCHED_FLIP'
    assert layer['tests'][0]['trip'] == 'u1' and layer['tests'][0]['choice'] is None
