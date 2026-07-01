from conciergent import ChatSurface, make_principal, parse_principal


def test_principal_round_trip():
    principal = make_principal(ChatSurface.slack, 'T1', 'U1')
    assert principal == 'slack:T1:U1'
    surface, parts = parse_principal(principal)
    assert surface == 'slack'
    assert parts == ('T1', 'U1')


def test_single_part_principal():
    principal = make_principal(ChatSurface.line, 'U9')
    assert principal == 'line:U9'
    surface, parts = parse_principal(principal)
    assert surface == 'line'
    assert parts == ('U9',)


def test_accepts_plain_surface_string():
    assert make_principal('teams', 'X') == 'teams:X'
