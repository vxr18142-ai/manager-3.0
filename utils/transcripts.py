# utils/transcripts.py
from __future__ import annotations
import discord


async def create_html_transcript(channel: discord.TextChannel) -> str:
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        author = str(msg.author)
        time = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        content = msg.content.replace("<", "&lt;").replace(">", "&gt;")

        messages.append(f"""
        <div class="msg">
            <span class="time">[{time}]</span>
            <span class="author">{author}:</span>
            <span class="content">{content}</span>
        </div>
        """)

    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial; background:#0b1d2a; color:#e6f1ff; }}
            .msg {{ margin:6px 0; }}
            .time {{ color:#5dade2; }}
            .author {{ font-weight:bold; color:#1ea7ff; }}
        </style>
    </head>
    <body>
        <h2>Transcript – #{channel.name}</h2>
        {''.join(messages)}
    </body>
    </html>
    """
    return html
