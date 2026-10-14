"""Batch enrichment of a page; no database calls in the rendering loop."""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import current_app
from . import db
from .formatting import BROKEN_IMAGE, render_text, safe_url


def package(rows, query='', details=False):
    if not rows:
        return []
    team_ids = tuple(sorted({row['team_id'] for row in rows}))
    placeholders = ','.join(['%s'] * len(team_ids))
    users, emojis = defaultdict(dict), defaultdict(dict)
    for user in db.select(f'SELECT team_id, slack_user_id, username FROM tbl_users WHERE team_id IN ({placeholders})', team_ids):
        users[user['team_id']][user['slack_user_id']] = user['username']
    for item in db.select(f'SELECT team_id, emoji_trigger, url FROM tbl_emojis WHERE team_id IN ({placeholders})', team_ids):
        emojis[item['team_id']][item['emoji_trigger']] = item['url']
    files, reactions = defaultdict(list), defaultdict(list)
    if details:
        ids = tuple(row['id'] for row in rows)
        placeholders = ','.join(['%s'] * len(ids))
        for file in db.select(f'SELECT message_id, name, mimetype, thumb_80, thumb_800, permalink, permalink_public FROM tbl_files WHERE message_id IN ({placeholders}) ORDER BY id', ids):
            url = next((safe_url(file[key]) for key in ('permalink_public', 'thumb_800', 'permalink') if safe_url(file[key])), BROKEN_IMAGE)
            thumb = next((safe_url(file[key], image=True) for key in ('thumb_80', 'thumb_800', 'permalink') if safe_url(file[key], image=True)), BROKEN_IMAGE)
            files[file['message_id']].append(dict(name=file['name'], mimetype=file['mimetype'], url=url, thumb=thumb))
        for reaction in db.select(f'''SELECT r.message_id, e.emoji_trigger, e.url, u.username
FROM tbl_reactions r JOIN tbl_emojis e ON e.id = r.emoji_id JOIN tbl_users u ON u.id = r.user_id
WHERE r.message_id IN ({placeholders}) ORDER BY r.id''', ids):
            reactions[reaction['message_id']].append((reaction['emoji_trigger'], safe_url(reaction['url'], image=True) or BROKEN_IMAGE, reaction['username']))
    zone = ZoneInfo(current_app.config['DISPLAY_TIMEZONE'])
    result = []
    for row in rows:
        record = dict(row)
        stamp = datetime.fromtimestamp(row['timestamp'], zone)
        record.update(date=stamp.strftime('%Y-%m-%d (%a)'), time=stamp.strftime('%I:%M %p'),
                      files=files[row['id']], reactions=reactions[row['id']])
        record['text'] = render_text(row['text'], users[row['team_id']], emojis[row['team_id']], query,
                                     row['is_image'], row['username'] == 'spoiler_alert')
        record['channel_name'] = row['channel_name'] or f"Unknown ({row['slack_channel_id']})"
        record['username'] = row['username'] or f"Unknown ({row['slack_user_id']})"
        record['full_name'] = row['full_name'] or f"Unknown ({record['username']})"
        record['avatar_url'] = safe_url(row['avatar_url'], image=True) or BROKEN_IMAGE
        record['archive_url'] = safe_url(row['archive_url'])
        result.append(record)
    return result
