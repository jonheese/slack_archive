"""Render Slack's stored text without treating messages as trusted HTML."""
import html
import re
from urllib.parse import urlsplit
import emoji
from markupsafe import Markup, escape

TOKEN = re.compile(r'```[\s\S]*?```|`[^`\n]+`|<[^>\n]+>|:[A-Za-z0-9_+\-]+:')
BROKEN_IMAGE = 'https://cdn1.iconfinder.com/data/icons/the-basics/100/link-broken-chain-512.png'


def safe_url(value, image=False):
    value = html.unescape(str(value or '')).strip()
    if any(ord(char) < 32 for char in value):
        return ''
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return ''
    if scheme in (('http', 'https') if image else ('http', 'https', 'mailto')):
        return value
    return ''


def plain(text, query):
    # Emphasis only operates on text, never on generated attributes or code.
    parts = re.split(r'(\*[^*\n]+\*|_[^_\n]+_)', text)
    output = []
    for part in parts:
        tag = None
        if len(part) > 2 and part[0] == part[-1] and part[0] in '*_':
            tag = 'b' if part[0] == '*' else 'i'
            part = part[1:-1]
        if query:
            pieces = re.split('(' + re.escape(query) + ')', part, flags=re.IGNORECASE)
            rendered = ''.join(f'<b>{escape(piece)}</b>' if i % 2 else str(escape(piece)) for i, piece in enumerate(pieces))
        else:
            rendered = str(escape(part))
        output.append(f'<{tag}>{rendered}</{tag}>' if tag else rendered)
    return ''.join(output)


def render_text(text, users, emojis, query='', is_image=False, spoiler=False):
    text = html.unescape(text or '')

    def inline(value):
        output, pos = [], 0
        for match in TOKEN.finditer(value):
            output.append(plain(value[pos:match.start()], query))
            token = match.group()
            if token.startswith('```'):
                output.append(f'<pre>{escape(token[3:-3].strip())}</pre>')
            elif token.startswith('`'):
                output.append(f'<pre class="inline">{escape(token[1:-1].strip())}</pre>')
            elif token.startswith('<@'):
                user_id = token[2:-1].split('|', 1)[0]
                output.append(str(escape('@' + (users.get(user_id) or user_id))))
            elif token.startswith('<'):
                target, _, label = token[1:-1].partition('|')
                url = safe_url(target)
                if url:
                    output.append(f'<a href="{escape(url)}">{escape(label or target)}</a>')
                    if is_image and safe_url(url, image=True):
                        output.append(f'<br /><img src="{escape(url)}" />')
                else:
                    output.append(str(escape(token)))
            else:
                url = safe_url(emojis.get(token[1:-1]), image=True)
                output.append(f'<img height="22px" width="22px" src="{escape(url)}" />' if url
                              else str(escape(emoji.emojize(token, language='alias'))))
            pos = match.end()
        output.append(plain(value[pos:], query))
        return ''.join(output)

    # Protect multiline code before handling line-based blockquotes.
    blocks = re.split(r'(```[\s\S]*?```)', text)
    output = []
    for block in blocks:
        if block.startswith('```') and block.endswith('```'):
            output.append(inline(block))
            continue
        quote = False
        full_quote = False
        lines = []
        for line in block.split('\n'):
            if line.startswith('>>>'):
                if not quote:
                    lines.append('<blockquote>')
                quote = full_quote = True
                line = line[3:]
            elif line.startswith('>') and not full_quote:
                if not quote:
                    lines.append('<blockquote>')
                quote = True
                line = line[1:]
            elif quote and not full_quote:
                lines.append('</blockquote>')
                quote = False
            if spoiler and ':' in line:
                prefix, rest = line.split(':', 1)
                lines.append(inline(prefix + ':') + '<span class="spoiler">' + inline(rest) + '</span>')
            else:
                lines.append(inline(line))
            lines.append('<br />')
        if lines and lines[-1] == '<br />':
            lines.pop()
        if quote:
            lines.append('</blockquote>')
        output.append(''.join(lines))
    return Markup(''.join(output))
