import time
import asyncio
from .base_plugin import BasePlugin

class LastSeen(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)

    def register_commands(self):
        self.bot.register_command('seen', self.plugin_name, self.seen_command)

    async def seen_command(self, params):
        """Shows when a user was last seen by the bot.
        Usage: <prefix>seen <user>"""
        msg = params['msg']
        muc = params['muc']
        nick = params['nick']
        jid = params['jid']

        parts = msg['body'].split(' ')

        if len(parts) == 1:
            target_nick = nick
            target_jid = jid
            start = 1
        else:
            target_nick = ' '.join(parts[1:])
            target_jid = await self.bot.get_jid_from_nick(muc, target_nick)
            start = 0

        last_message = self.bot.get_x_messages(muc, nick=target_nick, jid=target_jid, start=start)
        if last_message is None:
            mbody = f"No messages from {target_nick} found."
        else:
            timestamp = int(last_message['timestamp'])
            readable_time = self.get_readable_time(timestamp)
            mbody = f"User '{target_nick}' was last seen {readable_time} saying '{last_message['body']}'."
        await self.bot.send_message_processed(mbody, msg=msg)

    def get_readable_time(self, timestamp):
        current_time = int(time.time())
        time_diff = current_time - timestamp

        if time_diff < 60:  # Less than 1 minute
            seconds = time_diff
            return f"{seconds} second{'s' if seconds != 1 else ''} ago"
        elif time_diff < 3600:  # Less than 1 hour
            minutes = time_diff // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif time_diff < 86400:  # Less than 24 hours
            hours = time_diff // 3600
            minutes = (time_diff % 3600) // 60
            return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''} ago"
        elif time_diff < 2592000:  # Less than 30 days
            days = time_diff // 86400
            hours = (time_diff % 86400) // 3600
            return f"{days} day{'s' if days != 1 else ''} and {hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = time_diff // 86400
            return f"{days} day{'s' if days != 1 else ''} ago"