import asyncio
import dateparser
import time
from datetime import datetime
from .base_plugin import BasePlugin

class PostOffice(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)

    def register_commands(self):
        self.bot.register_command('tell', self.plugin_name, self.tell_command, cooldown=self.cooldown)
        self.bot.register_command('remindme', self.plugin_name, self.remindme_command, cooldown=self.cooldown)
        self.bot.register_command('cleartells', self.plugin_name, self.clear_tells_command, admin=True, category='admin')

    def register_variables(self):
        self.cooldown = self.config.get("cooldown", 60)
        self.filter = self.config.get("filter", None) if self.bot.llm_enabled else None
        self.allkeys = self.bot.sql.get_all_keys(self.plugin_name)
        self.reminders = {key: value for key, value in self.allkeys.items() if key.startswith("remind_")}

    def register_tasks(self):
        self.create_task(self._schedule_reminders)

    async def _schedule_reminders(self):
        now = int(time.time())
        for key, reminders in self.reminders.items():
            muc_nick = key.split('_', 1)[1]
            for timestamp_str, message in reminders.items():
                timestamp = int(timestamp_str)
                if timestamp > now:
                    await self.create_scheduled_task(
                        lambda: self._send_reminder(muc_nick, message, timestamp),
                        timestamp,
                        task_name=f"remind_{muc_nick}_{timestamp}"
                    )

    async def _send_reminder(self, muc_nick, message, timestamp):
        muc, nick = muc_nick.rsplit('_', 1)
        mbody = f"🔔 Reminder for {nick}: {message}"
        if self.filter:
            mbody = await self.bot.llm.send_prompt(self.filter, {"text": mbody})
        await self.bot.send_message_processed(mbody, mto=muc, mtype='groupchat')
        self.bot.sql.delete(self.plugin_name, f"remind_{muc}_{nick}", timestamp)

    async def tell_command(self, params):
        """Deliver a message to another user
        Usage: <prefix>tell <user> <message>, [reply]<prefix>tell <user>"""
        extras = await self.get_extras_message(params)
        prefix = extras['prefix']
        msg = params['msg']
        muc = params['muc']
        nick = params['nick']
        quote = params['quote']

        parts = msg['body'].split(' ')

        if len(parts) < 3 and not quote:
            return

        if parts[1].startswith('"'):
            recipient = ' '.join(parts[1:]).split('"')[1]
            parts = [''] + msg['body'].split(' ', 1)[1].split(recipient)
        else:
            recipient = parts[1]

        if recipient == nick:
            mbody = f"Use '{prefix}remindme' instead."
            return

        tell_message = quote or ' '.join(parts[2:])

        old_tell = self._add_tell(muc, recipient, nick, tell_message)
        if old_tell:
            mbody = f"Sure, I will tell {recipient} {tell_message} for you instead of {old_tell} when I see them. 📨"
        else:
            mbody = f"Sure, I will tell {recipient} {tell_message} for you when I see them. 📨"
        if self.filter:
            mbody = await self.bot.llm.send_prompt(self.filter, {"text": mbody})
        await self.bot.send_message_processed(mbody, msg=msg)

    async def remindme_command(self, params):
        """I'll remind you of something in the future!
        Usage: [quote]<prefix>remindme <time>
        Example:
        >do the dishes
        <prefix>remindme 2 hours"""
        msg = params['msg']
        muc = params['muc']
        nick = params['nick']
        quote = params['quote']

        parts = msg['body'].split(' ', 1)

        if len(parts) == 1 or not quote:
            return

        timepart = parts[1]
        reminddate = dateparser.parse(timepart, settings={'PREFER_DATES_FROM': 'future'})

        if reminddate is None:
            mbody = self.bot.send_message_processed("Sorry, I couldn't understand the time you provided. 🕒", msg=msg)
        else:
            now = datetime.now()

            if reminddate > now:
                self._add_reminder(muc, nick, reminddate, quote)
                mbody = f"Sure, I will remind you of '{quote}' in {timepart}. ⏳🔔"
            else:
                mbody = f"The time you provided is in the past. I can't remind you of '{quote}' for a past time. ⏮️🕒"

        if self.filter:
            mbody = await self.bot.llm.send_prompt(self.filter, {"text": mbody})
        await self.bot.send_message_processed(mbody, msg=msg)

    async def clear_tells_command(self, params):
        msg = params['msg']
        muc = params['muc']
        extras = await self.get_extras_cmd(params)
        prefix = extras['prefix']
        parts = msg['body'].strip().split(' ')

        if len(parts) < 3 or parts[2].lower() != 'yes':
            mbody = f"This will delete all tells from {muc}. To confirm, type '{prefix}cleartells {muc} yes'."
            await self.bot.send_message_processed(mbody, msg=msg)
            return

        self.bot.sql.delete_all_fields(self.plugin_name, key_pattern=f"{muc}_")

        mbody = f"All tells from {muc} have been deleted."
        await self.bot.send_message_processed(mbody, msg=msg)

    async def handle_groupchat_message(self, params):
        extras = await self.get_extras_message(params)
        muc = extras['muc']
        nick = extras['nick']
        tells = self.bot.sql.get_all_fields(self.plugin_name, f"tell_{muc}_{nick}")
        for sender, message in tells.items():
            mbody = f"📨 {nick}, {sender} wanted me to tell you {message}"
            if self.filter:
                mbody = await self.bot.llm.send_prompt(self.filter, {"text": mbody})
            self.bot.sql.delete(self.plugin_name, f"tell_{muc}_{nick}", sender)
            await self.bot.send_message_processed(mbody, msg=params['msg'])
            return

    def _add_tell(self, muc, recipient_nick, sender_nick, message):
        old_tell = self.bot.sql.get(self.plugin_name, f"tell_{muc}_{recipient_nick}", sender_nick, None)
        self.bot.sql.set(self.plugin_name, f"tell_{muc}_{recipient_nick}", sender_nick, message)
        return old_tell

    def _add_reminder(self, muc, recipient_nick, reminddate, message):
        timestamp = int(reminddate.timestamp())
        self.bot.sql.set(self.plugin_name, f"remind_{muc}_{recipient_nick}", timestamp, message)

        if timestamp > int(time.time()):
            self.create_scheduled_task(
                lambda: self._send_reminder(f"{muc}_{recipient_nick}", message, timestamp),
                timestamp,
                task_name=f"reminder_{muc}_{recipient_nick}_{timestamp}"
            )
