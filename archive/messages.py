"""Load a page's related data in batches, then build template-ready messages."""

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app

from . import db
from .formatting import BROKEN_IMAGE, first_safe_url, render_text, safe_url


def load_team_lookups(rows):
    team_ids = tuple(sorted({row["team_id"] for row in rows}))
    placeholders = db.placeholders(team_ids)
    users = defaultdict(dict)
    emojis = defaultdict(dict)
    user_rows = db.select(
        f"SELECT team_id, slack_user_id, username FROM tbl_users WHERE team_id IN ({placeholders})",
        team_ids,
    )
    for user in user_rows:
        users[user["team_id"]][user["slack_user_id"]] = user["username"]
    emoji_rows = db.select(
        f"SELECT team_id, emoji_trigger, url FROM tbl_emojis WHERE team_id IN ({placeholders})",
        team_ids,
    )
    for item in emoji_rows:
        emojis[item["team_id"]][item["emoji_trigger"]] = item["url"]
    return users, emojis


def build_attachment(file):
    return {
        "name": file["name"],
        "mimetype": file["mimetype"],
        "url": first_safe_url(
            [file["permalink_public"], file["thumb_800"], file["permalink"]]
        ),
        "thumb": first_safe_url(
            [file["thumb_80"], file["thumb_800"], file["permalink"]], image=True
        ),
    }


def load_files(message_ids):
    files = defaultdict(list)
    rows = db.select(
        f"""SELECT message_id, name, mimetype, thumb_80, thumb_800, permalink, permalink_public
FROM tbl_files WHERE message_id IN ({db.placeholders(message_ids)}) ORDER BY id""",
        message_ids,
    )
    for file in rows:
        files[file["message_id"]].append(build_attachment(file))
    return files


def load_reactions(message_ids):
    reactions = defaultdict(list)
    rows = db.select(
        f"""SELECT r.message_id, e.emoji_trigger, e.url, u.username
FROM tbl_reactions r JOIN tbl_emojis e ON e.id = r.emoji_id JOIN tbl_users u ON u.id = r.user_id
WHERE r.message_id IN ({db.placeholders(message_ids)}) ORDER BY r.id""",
        message_ids,
    )
    for reaction in rows:
        reactions[reaction["message_id"]].append(
            {
                "emoji_trigger": reaction["emoji_trigger"],
                "image_url": safe_url(reaction["url"], image=True) or BROKEN_IMAGE,
                "username": reaction["username"],
            }
        )
    return reactions


def build_display_message(row, *, users, emojis, files, reactions, query, zone):
    record = dict(row)
    stamp = datetime.fromtimestamp(row["timestamp"], zone)
    record.update(
        date=stamp.strftime("%Y-%m-%d (%a)"),
        time=stamp.strftime("%I:%M %p"),
        files=files,
        reactions=reactions,
    )
    record["text"] = render_text(
        row["text"],
        users,
        emojis,
        query=query,
        is_image=row["is_image"],
        spoiler=row["username"] == "spoiler_alert",
    )
    record["channel_name"] = (
        row["channel_name"] or f"Unknown ({row['slack_channel_id']})"
    )
    record["username"] = row["username"] or f"Unknown ({row['slack_user_id']})"
    record["full_name"] = row["full_name"] or f"Unknown ({record['username']})"
    record["avatar_url"] = safe_url(row["avatar_url"], image=True) or BROKEN_IMAGE
    record["archive_url"] = safe_url(row["archive_url"])
    return record


def prepare_messages(rows, query="", *, include_details=False):
    if not rows:
        return []
    users, emojis = load_team_lookups(rows)
    files, reactions = {}, {}
    if include_details:
        message_ids = tuple(row["id"] for row in rows)
        files = load_files(message_ids)
        reactions = load_reactions(message_ids)
    zone = ZoneInfo(current_app.config["DISPLAY_TIMEZONE"])
    return [
        build_display_message(
            row,
            users=users[row["team_id"]],
            emojis=emojis[row["team_id"]],
            files=files.get(row["id"], []),
            reactions=reactions.get(row["id"], []),
            query=query,
            zone=zone,
        )
        for row in rows
    ]
