import random
import asyncio
import datetime
from .base_plugin import BasePlugin

class ChatPlugin(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)

    def register_variables(self):
        self.num_history_msgs = self.config.get('num_history_msgs', 5)
        self.chat_prompt = self.config.get('chat_prompt')
        self.styles = self.config.get('styles', ['Not provided'])
        self.moods = self.config.get('moods', ['Not provided'])
        self.cooldowns = {}
        self.cooldown_duration = self.config.get('cooldown', 60)

    async def handle_groupchat_message(self, params):
        if not self.bot.llm_enabled:
            self.log.warning(f"llm not enabled, you should set it or disable the chat plugin.")
            return
        if not self.chat_prompt:
            self.log.warning(f"No chat prompt set, .")
            return
        if params['is_command']:
            return
        msg = params['msg']
        body = msg['body']
        actions_prefix = self.bot.plugin_config.get('actions', {}).get('prefix', ':')
        if body.startswith(actions_prefix):
            return
        muc = msg.get('mucroom')
        nick = msg.get('mucnick')
        jid =  await self.bot.get_jid_from_nick(muc, nick)
        user = jid or nick
        config = self.bot.mucs.get(muc, {})
        bot_nick = config.get('nick') or self.bot.nick
        if bot_nick.lower() not in body.lower():
            return
        if user in self.cooldowns:
            remaining = (datetime.datetime.now() - self.cooldowns[user]).total_seconds()
            if remaining < self.cooldown_duration:
                self.log.debug(f"{user} is still in cooldown for {int(self.cooldown_duration - remaining)} seconds, notifying")
                await self.bot.send_message_processed(
                    f"Command '{bot_nick}' is on cooldown. Try again in {int(self.cooldown_duration - remaining) + 1} second(s).",
                    mto = f"{muc}/{nick}",
                    mtype = 'chat')
                return

        history = self.bot.get_x_messages(muc, self.num_history_msgs)
        formatted_history = []
        added_messages = set()

        if not isinstance(history, list):
            history = [history] # in case history == 1

        for message in history:
            message_body = message['body']

            if message_body in added_messages:
                continue

            timestamp = int(message['timestamp'])
            dt = datetime.datetime.fromtimestamp(timestamp)
            time_str = dt.strftime('[%H:%M:%S]')

            nick = message['nick']

            formatted_message = f"{time_str}{nick}: {message_body}"
            formatted_history.append(formatted_message)

            added_messages.add(message_body)

        formatted_history = formatted_history[::-1]
        formatted_history = '\n'.join(formatted_history)

        style = random.choice(self.styles)
        mood = random.choice(self.moods)

        mbody = await self.bot.llm.send_prompt(self.chat_prompt, {
            'history': formatted_history,
            'bot_nick': bot_nick,
            'style': style,
            'mood': mood
        })
        await self.bot.send_message_processed(mbody, msg=msg)

        self.cooldowns[user] = datetime.datetime.now()
        self.cooldowns = {k: v for k, v in self.cooldowns.items()
                         if (datetime.datetime.now() - v).total_seconds() < self.cooldown_duration * 2}


    # register dummy command to show in help
    def register_commands(self):
        self.bot.register_command('chat', self.plugin_name, self.chat_command)

    async def chat_command(self, params):
        """Chat with me!
        Usage: Just say my name :3c"""
        pass
