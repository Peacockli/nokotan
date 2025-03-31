import re
import random
import asyncio
from .base_plugin import BasePlugin

class KeyWords(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)

    def register_variables(self):
        self.keywords = self.parse_keywords(self.get_data("keywords", "plaintext"))
        self.strict_keywords = self.parse_keywords(self.get_data("strict_keywords", "plaintext"))
        self.filter = self.config.get("filter", None)

    def parse_keywords(self, data):
        parsed = []
        for line in data:
            if not line.strip():
                continue
            chance, dm_mode, triggers, responses = line.split(';')
            chance = float(chance)
            is_dm = dm_mode.lower() == 'dm'
            triggers = [trigger.strip() for trigger in triggers.split('|')]
            responses = [response.strip().replace('\\n', '\n') for response in responses.split('|')]
            parsed.append({
                "chance": chance,
                "dm": is_dm,
                "triggers": triggers,
                "responses": responses
            })
        return parsed

    async def handle_groupchat_message(self, params):
        if params["is_command"]:
            return
        if not self.keywords and not self.strict_keywords:
                    return

        msg = params["msg"]
        nick = msg.get('mucnick', '')
        message_body = msg['body'].lower()
        message_body = re.sub(r'[^\w\s]', '', message_body) # punctuation, numbers
        message_body = re.sub(r'(.)\1+', r'\1', message_body) # consecutive repeat letters
        responses = []

        for data in self.keywords:
            chance = data.get("chance", 1.0)
            triggers = data.get("triggers", [])
            is_dm = data.get("dm", False)
            for trigger in triggers:
                nick_trigger = None
                if trigger.startswith('user:'):
                    nick_trigger = trigger.replace('user:', '')
                if trigger in message_body or nick_trigger == nick:
                    if random.random() <= chance:
                        responses_list = data.get("responses", [])
                        if responses_list:
                            responses.append((random.choice(responses_list), is_dm))
                    break

        for data in self.strict_keywords:
            chance = data.get("chance", 1.0)
            triggers = data.get("triggers", [])
            is_dm = data.get("dm", False)
            for trigger in triggers:
                if trigger.lower() == message_body:
                    if random.random() <= chance:
                        responses_list = data.get("responses", [])
                        if responses_list:
                            responses.append((random.choice(responses_list), is_dm))
                    break

        if responses:
            response, is_dm = random.choice(responses)
            if response.startswith("react:"):
                response = response.replace("react:", "")
                self.bot.send_reactions(response, msg=msg)
                return
            if self.filter and self.bot.llm_enabled:
                response = await self.bot.llm.send_prompt(self.filter, {"text": response})
            if is_dm:
                nick = msg['mucnick']
                muc = msg['mucroom']
                mto = f"{muc}/{nick}"
                await self.bot.send_message_processed(response, mto=mto, mtype='chat')
            else:
                await self.bot.send_message_processed(response, msg=msg)
