from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
import re
import pytest
from mysql.connector import Error
from archive import create_app, db, messages, queries
from archive.formatting import render_text, safe_url


@pytest.fixture
def app():
    return create_app(dict(TESTING=True, DB_HOST='unused', DB_NAME='unused', DB_USER='unused', DB_PASSWORD='unused'))


def row(identifier=1, **changes):
    result = dict(id=identifier, team_id=1, channel_id=2, team_name='team', username='jon',
                  timestamp=1700000000, text='test message', channel_name='general', slack_channel_id='C1',
                  slack_user_id='U1', full_name='Jon', avatar_url='', archive_url='', is_image=0)
    result.update(changes)
    return result


def fake_select(monkeypatch, rows, total=40):
    calls = []
    def select(sql, params=()):
        calls.append((sql, params))
        if 'COUNT(*)' in sql:
            return [dict(total=total)]
        if 'FROM tbl_users WHERE' in sql:
            return [dict(team_id=1, slack_user_id='U1', username='jon')]
        if 'FROM tbl_emojis WHERE' in sql:
            return [dict(team_id=1, emoji_trigger='party', url='https://example.org/party.png')]
        if 'FROM tbl_files' in sql or 'FROM tbl_reactions' in sql:
            return []
        return rows
    monkeypatch.setattr(db, 'select', select)
    return calls


def test_search_query_count_is_constant(app, monkeypatch):
    for size in (1, 20, 100):
        calls = fake_select(monkeypatch, [row(i) for i in range(size)])
        response = app.test_client().get('/?q=test&rpp=100')
        assert response.status_code == 200
        assert len(calls) == 4
        assert not any('tbl_files' in sql or 'tbl_reactions' in sql for sql, _ in calls)
        assert calls[0][1] == ('+test',)
        assert 'Server-Timing' in response.headers


def test_empty_search_does_not_connect(app, monkeypatch):
    select = Mock(side_effect=AssertionError('Unexpected query'))
    monkeypatch.setattr(db, 'select', select)
    assert app.test_client().get('/').status_code == 200
    select.assert_not_called()


def test_parameters_and_exclusion(app):
    with app.app_context():
        options = queries.filters(dict(q="it's 100%_!", exact_text='on', team_name='-friends'))
        sql, params = queries.where_clause(options)
        assert "it's" not in sql
        assert params == ('%friends%', "%it's 100!%!_!!%")
        assert 'NOT LIKE' in sql


def test_fulltext_input_cannot_inject_boolean_operators():
    assert queries.fulltext_query('server restart') == '+server +restart'
    assert queries.fulltext_query('++server -restart @"test"* (server)') == '+server +restart +test'
    assert queries.fulltext_query('!!!') == ''


def test_case_and_legacy_flags():
    assert not queries.filters({})['exact_text']
    assert queries.filters({'case_sensitive': 'on'})['exact_text']
    assert not queries.filters({'sql_match': 'True'})['exact_text']
    assert queries.filters({'sql_match': 'False'})['exact_text']
    assert not queries.filters({'exact_text': 'False', 'sql_match': 'False'})['exact_text']


@pytest.mark.parametrize('url', ['/?page=bad', '/?page=0', '/?rpp=0', '/?rpp=201', '/?start_date=nope', '/context/not-an-id/', '/context/1/?offset=nope'])
def test_invalid_inputs(app, url):
    assert app.test_client().get(url).status_code == 400


def test_missing_context(app, monkeypatch):
    monkeypatch.setattr(db, 'select', lambda *a: [])
    assert app.test_client().get('/context/999/').status_code == 404


def test_database_errors_are_safe(app, monkeypatch):
    monkeypatch.setattr(db, 'select', Mock(side_effect=Error('secret credentials')))
    for url in ('/?q=test', '/context/1/'):
        response = app.test_client().get(url)
        assert response.status_code == 503
        assert b'secret credentials' not in response.data


def test_pagination_encodes_and_preserves_filters(app, monkeypatch):
    fake_select(monkeypatch, [row()], total=40)
    response = app.test_client().get('/', query_string=dict(q='a&b+c', exact_text='on', case_sensitive='on', username='jon'))
    from html import unescape
    link = re.search(r"href='([^']+)'>&?Next Page", response.text)
    assert link
    params = parse_qs(urlsplit(unescape(link[1])).query)
    assert params['q'] == ['a&b+c']
    assert params['exact_text'] == ['True']
    assert params['case_sensitive'] == ['True']
    assert params['username'] == ['jon']


def test_context_batching_and_filters(app, monkeypatch):
    calls = fake_select(monkeypatch, [row()])
    response = app.test_client().post('/context/1/', data=dict(q='test', exact_text='True', case_sensitive='True'))
    assert response.status_code == 200
    assert len(calls) == 7
    assert 'm.id <=' in calls[1][0] and 'm.id >' in calls[2][0]
    assert 'name=\'case_sensitive\' value=\'True\'' in response.text
    assert 'name=\'exact_text\' value=\'True\'' in response.text


def test_context_offsets_and_timestamp_ties(app, monkeypatch):
    source = [row(i, timestamp=1700000000 + i // 3) for i in range(1, 61)]
    anchor = source[29]
    def select(sql, params):
        if 'WHERE m.id = ' in sql:
            return [anchor]
        channel, stamp, _, identifier, limit, offset = params
        key = stamp, identifier
        before = 'DESC' in sql
        rows = [item for item in source if ((item['timestamp'], item['id']) <= key if before else (item['timestamp'], item['id']) > key)]
        rows.sort(key=lambda item: (item['timestamp'], item['id']), reverse=before)
        return rows[offset:offset+limit]
    monkeypatch.setattr(db, 'select', select)
    with app.app_context():
        for offset in (-20, -1, 0, 1, 20):
            _, found = queries.context(30, offset)
            expected = source[max(0, 20+offset):max(0, 40+offset)]
            assert [x['id'] for x in found] == [x['id'] for x in expected]


def test_rendering_is_safe_and_preserves_slack_features():
    rendered = str(render_text('<script>alert(1)</script> <@U1>, :smile: <https://example.org/a_b|label> `*code*` *bold*', {'U1': 'Jon'}, {}))
    assert '<script>' not in rendered
    assert '@Jon,' in rendered
    assert '😄' in rendered
    assert 'href="https://example.org/a_b">label</a>' in rendered
    assert '<pre class="inline">*code*</pre>' in rendered
    assert '<b>bold</b>' in rendered
    assert 'href=' not in render_text('<javascript:alert(1)>', {}, {})
    assert safe_url('http://[') == ''


def test_highlight_does_not_modify_attributes():
    rendered = render_text('<https://example.org/test|test>', {}, {}, query='test')
    assert 'href="https://example.org/test"' in rendered
    assert '<b>' not in rendered


def test_quotes_code_and_spoiler():
    assert '<blockquote>' in render_text('&gt; quoted\nnormal', {}, {})
    assert '<pre>&lt;unsafe&gt;\n*literal*</pre>' in render_text('```<unsafe>\n*literal*```', {}, {})
    assert '<span class="spoiler"> secret</span>' in render_text('Spoiler: secret', {}, {}, spoiler=True)


def test_team_scoped_lookups(app, monkeypatch):
    def select(sql, params):
        if 'tbl_users' in sql:
            return [dict(team_id=t, slack_user_id='U1', username=f'user{t}') for t in (1, 2)]
        return []
    monkeypatch.setattr(db, 'select', select)
    with app.app_context():
        records = messages.package([row(text='<@U1>'), row(2, team_id=2, text='<@U1>')])
    assert [str(item['text']) for item in records] == ['@user1', '@user2']


def test_template_autoescaping(app, monkeypatch):
    fake_select(monkeypatch, [row(username='<script>bad</script>', text='*okay*')])
    text = app.test_client().get('/?q=test').text
    assert '<script>bad</script>' not in text
    assert '&lt;script&gt;bad&lt;/script&gt;' in text
    assert '<b>okay</b>' in text


def test_connection_reuse_and_cleanup(app, monkeypatch):
    conn = Mock()
    conn.cursor.return_value.fetchall.return_value = []
    connect = Mock(return_value=conn)
    monkeypatch.setattr(db.mysql.connector, 'connect', connect)
    with app.app_context():
        db.select('SELECT %s', ("it's",))
        db.select('SELECT %s', ('other',))
        assert connect.call_count == 1
        assert conn.cursor.return_value.execute.call_args.args == ('SELECT %s', ('other',))
        assert conn.cursor.return_value.close.call_count == 2
    conn.close.assert_called_once()


def test_connection_cleanup_on_error(app, monkeypatch):
    conn = Mock()
    conn.cursor.return_value.execute.side_effect = Error('failure')
    monkeypatch.setattr(db.mysql.connector, 'connect', Mock(return_value=conn))
    with pytest.raises(Error), app.app_context():
        db.select('SELECT 1')
    conn.cursor.return_value.close.assert_called_once()
    conn.close.assert_called_once()


def test_enrichment_attaches_multiple_files_and_reactions(app, monkeypatch):
    def select(sql, params):
        if 'FROM tbl_files' in sql:
            return [dict(message_id=1, name='image', mimetype='image/png', thumb_80='https://example.org/small.png',
                         thumb_800=None, permalink='https://example.org/full.png', permalink_public=None)]
        if 'FROM tbl_reactions' in sql:
            return [dict(message_id=1, emoji_trigger='smile', url='https://example.org/smile.png', username=name)
                    for name in ('jon', 'friend')]
        return []
    monkeypatch.setattr(db, 'select', select)
    with app.app_context():
        found = messages.package([row(), row(2, text=None)], details=True)
    assert len(found) == 2
    assert len(found[0]['files']) == 1
    assert len(found[0]['reactions']) == 2
    assert found[0]['files'][0]['url'] == 'https://example.org/full.png'
    assert found[1]['files'] == [] and found[1]['reactions'] == []
    assert found[1]['text'] == ''


def test_exclusive_dates_use_display_timezone(app):
    with app.app_context():
        sql, params = queries.where_clause(queries.filters(dict(start_date='2024-01-01', end_date='2024-01-02')))
    assert 'm.timestamp > %s' in sql and 'm.timestamp < %s' in sql
    assert params == (1704085200, 1704171600)


def test_late_enrichment_failure_returns_503(app, monkeypatch):
    def select(sql, params=()):
        if 'COUNT(*)' in sql:
            return [dict(total=1)]
        if 'FROM tbl_users WHERE' in sql:
            raise Error('private database detail')
        return [row()]
    monkeypatch.setattr(db, 'select', select)
    response = app.test_client().get('/?q=test')
    assert response.status_code == 503
    assert b'private database detail' not in response.data
