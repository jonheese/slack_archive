# Slack Archive

Flask search and context browser for the existing Slack archive MySQL schema.

## Changes in this branch

- One MySQL connection per request, closed at teardown, with bound SQL parameters.
- Four queries for a populated search page; at most seven for a context window.
  Lookup data is scoped by team and loaded once. Attachments/reactions are batched
  only for context pages, where they are displayed. No cross-request cache or
  background service is required.
- Full-text search is the default. Ordinary input is converted to required words
  (`server restart` becomes `+server +restart`) using the existing full-text index.
  Boolean operators supplied by users are treated as punctuation, not commands.
  MySQL's configured stopwords and token-length limits still apply. If the input
  contains only ignored words or punctuation, no results are returned; use
  **Match exact text** to find those strings. No automatic slow substring fallback.
- **Match exact text** searches for a literal substring, including `%` and `_`.
  It does not mean whole-message equality. **Case-Sensitive** implies literal
  matching. Otherwise matching uses the existing database collation.
- Search results remain chronological. Full-text results are not relevance-sorted.
  Multiword input is not a phrase unless literal matching is selected.
- Existing explicit `sql_match=True/on` links use full-text search; explicit false
  selects literal search. Old links with no mode parameter now use the new default.
- Context navigation uses timestamp and ID together to handle equal timestamps.
- Dates retain the old exclusive midnight boundaries: `timestamp > start_date`
  and `timestamp < end_date`, in America/New_York (configurable).
- `.j2` templates are autoescaped. Only the renderer's escaped Slack formatting is
  trusted HTML. Links allow HTTP/HTTPS/mailto; images allow HTTP/HTTPS. Formatting,
  code, mentions, emojis, links, quotes, and spoilers are supported. Malformed raw
  HTML is displayed as text rather than executed. Message-link labels are honored.
- Invalid pagination/date inputs return 400; missing context messages return 404;
  database errors return 503 without exposing database details. `rpp` is 1–200.
- Production uses two synchronous Gunicorn workers. No application writes or
  automatic database migrations are performed.

## Existing indexes

The supplied production schema already has a full-text index on `tbl_messages.text`,
indexes on `(channel_id, timestamp)` and `timestamp`, unique ID indexes, and a
reaction index beginning with `message_id`. Do not add duplicates.

Jon applied the following index on 2026-10-14. **Do not run it again on ajax**:

```sql
ALTER TABLE slack_archive.tbl_files
    ADD INDEX idx_files_message_id (message_id),
    ALGORITHM=INPLACE,
    LOCK=NONE;
```

No other schema changes are required. Query plans and additional indexes should
be evaluated after measuring the refactor with production data.

## Local development and tests

Use Python 3.12:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests use synthetic messages and mocked database responses/connections. They
verify constant query counts, parameter binding, rendering/escaping, navigation,
team lookups, validation, and resource cleanup. They do not validate real MySQL
execution plans or prove production latency. No production data is included.

Copy `config-dist.py` to `config.py` and set its DB values to run the app.
Optional settings: `DB_PORT = 3306`, `DISPLAY_TIMEZONE = 'America/New_York'`.

## Run a parallel test copy on ajax

Run the following **from the existing production repository directory**. It
creates a separate checkout and leaves the current checkout/container running.
If `../slack_archive-test` already exists, choose a different unused directory.

```sh
git fetch origin
git worktree add --detach ../slack_archive-test origin/refactor/query-performance
cp config.py ../slack_archive-test/config.py
cd ../slack_archive-test
docker compose -p slack-archive-test -f compose.test.yml up -d --build
docker compose -p slack-archive-test -f compose.test.yml logs --tail=50
```

The standalone test Compose file uses a different image tag and service name.
It binds **127.0.0.1:5002**, while production keeps port 5001. It uses the copied
DB credentials and the same database. Do not combine it with docker-compose.yml.
A DB host reachable from the current default Docker network should also be
reachable from the test network; verify the startup logs and a search request.

From your workstation (using your usual SSH user/host options):

```sh
ssh -N -L 5002:127.0.0.1:5002 ajax
```

Open http://localhost:5002 in your browser. No Apache changes are needed for the
comparison. If local port 5002 is occupied, change only the first port in `-L`.

Compare several repeated runs of:

1. `test`, default search (all-word full-text).
2. `test`, with **Match exact text** checked (old substring comparison).
3. A context view and its Back/Forward 1 and 20 buttons.
4. Multiword searches, literal punctuation, case-sensitive searches, team/user/
   channel exclusions, and date filters.
5. Messages containing code, mentions, links, custom emojis, spoilers, attachments,
   and reactions. Check the same messages on production to verify appearance.

Post-index baseline supplied by Jon: substring `test` ~24s; old natural-language
`test` ~6s; context ~8s. Full-text multiword results intentionally change from
any-word matching to all searchable words. Repeated runs help separate cache and
host-load effects; query-count reductions are not themselves latency measurements.

```sh
docker compose -p slack-archive-test -f compose.test.yml logs -f
```

Application logs include query count, cumulative query time (including the first
connection setup), and total request time, without SQL/search text. Browser Network
responses also include `Server-Timing`. Browser image downloads happen after the
HTML response and are not included in server timings.

Stop the test instance from its checkout:

```sh
docker compose -p slack-archive-test -f compose.test.yml down
```

## Production rollout after acceptance

Merge the reviewed PR, then from the original production checkout:

```sh
git rev-parse HEAD  # Record this commit for rollback.
git pull --ff-only origin master
docker compose up -d --build
docker compose logs --tail=50
```

The production service retains its existing name, volumes and port 5001; Apache
can keep its current target. Rebuilding/replacing the container briefly interrupts
service. For rollback, check out the recorded prior commit in the production
checkout and run `docker compose up -d --build` again. The added file index is
compatible with both versions and does not need to be removed.
