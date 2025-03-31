import time
import logging

logger = logging.getLogger(__name__)

class Command:
    def __init__(self, command_handler, name, plugin, function, category=None, aliases=None, cooldown=2, hidden=False, admin=False):
        self.command_handler = command_handler
        self.name = name
        self.plugin = plugin
        self.function = function
        self.category = category
        self.aliases = aliases or []
        self.cooldown = cooldown
        self.hidden = hidden
        self.admin = admin
        self.last_used = {}

        if self.category:
            if self.category not in self.command_handler.categories:
                self.command_handler.categories[self.category] = []
            self.command_handler.categories[self.category].append(self)

        logger.debug(f"Command '{self.name}' initialized from plugin '{self.plugin}' with cooldown {self.cooldown}s, hidden={self.hidden}, admin={self.admin}")


    async def call(self, command_params):
        bot = command_params['bot']
        muc = command_params['muc']
        nick = command_params['nick']
        jid = command_params['jid']

        user = f"{muc}/{nick}" if muc else jid
        current_time = time.time()
        last_used_time = self.last_used.get(user, 0)
        category_cooldown = False

        if self.category:
            for command in self.command_handler.categories.get(self.category, []):
                last_used_in_category = command.last_used.get(user, 0)
                if last_used_in_category and last_used_in_category > last_used_time:
                    category_cooldown = True
                    last_used_time = last_used_in_category

        if current_time - last_used_time < self.cooldown:
            if category_cooldown:
                logger.info(f"Commands of category {self.category} are on cooldown for '{user}'")
            else:
                logger.info(f"Command '{self.name}' is on cooldown for user '{user}'")
            return await self.cooldown_message(
                bot,
                int(self.cooldown - (current_time - last_used_time)),
                self.name,
                user,
                category_cooldown
            )
        self.last_used[user] = current_time
        logger.debug(f"Executing command '{self.name}' for user '{user}'")
        return await self.function(command_params)

    async def cooldown_message(self, bot, remaining, command_name, user, category_cooldown):
        if category_cooldown:
            body = f"Commands of category '{self.category}' are on cooldown. Try again in {remaining + 1} second(s)."
            logger.info(f"Sending cooldown message to user '{user}' for command category '{self.category}'")
        else:
            body = f"Command '{command_name}' is on cooldown. Try again in {remaining + 1} second(s)."
            logger.info(f"Sending cooldown message to user '{user}' for command '{command_name}'")
        await bot.send_message_processed(mto=user, mbody=body, mtype="chat")

class CommandHandler:
    def __init__(self, bot):
        self.bot = bot
        self.commands = {}
        self.categories = {}
        self.show_command_suggestion = self.bot.show_command_suggestion
        self.run_command_suggestion = self.bot.run_command_suggestion
        logger.debug("CommandHandler initialized")

    def register_command(self, name, plugin, function, category=None, aliases=None, cooldown=None, hidden=False, admin=False):
        logger.info(f"Command '{name}' registered from plugin '{plugin}'")
        cmd = Command(self, name, plugin, function, category, aliases, cooldown or self.bot.default_command_cooldown, hidden, admin)
        self.commands[name] = cmd

    async def handle_groupchat_message(self, msg, quote):
        muc = msg['from'].bare
        nick = msg['from'].resource
        prefix = self.bot.mucs.get(muc, {}).get("command_prefix", self.bot.default_prefix)
        return await self._handle_command(msg, prefix, muc=muc, nick=nick, quote=quote)

    async def handle_private_message(self, msg):
        jid = msg['from'].bare
        prefix = self.bot.default_prefix
        return await self._handle_command(msg, prefix, jid=jid)

    async def handle_whisper(self, msg):
        muc = msg['from'].bare
        nick = msg['from'].resource
        prefix = self.bot.mucs.get(muc, {}).get("command_prefix", self.bot.default_prefix)
        return await self._handle_command(msg, prefix, muc=muc, nick=nick)

    async def _handle_command(self, msg, prefix, muc=None, nick=None, jid=None, quote=None):
        if not jid and muc and nick:
            jid = await self.bot.get_jid_from_nick(muc, nick)

        is_command = False
        body = msg['body'].strip().lower()
        if not body.startswith(prefix) or len(body) <= len(prefix):
            return is_command

        cmd_name = body[len(prefix):].split()[0]
        logger.debug(f"Checking if message '{body}' from {nick or jid} is a command. Prefix: '{prefix}', potential command: '{cmd_name}'")

        for command in self.commands.values():
            if cmd_name in command.aliases:
                logger.debug(f"{cmd_name} found in {command} aliases.")
                cmd_name = command.name

        if cmd_name not in self.commands:
            if self.show_command_suggestion:
                logger.debug(f'Suggesting command for invalid input by {nick or jid}: {cmd_name}')
                cmd_name = await self._send_suggested_command(cmd_name, muc, nick, jid)
                if cmd_name is None:
                    return is_command
            else:
                logger.debug(f"Command '{cmd_name}' not found in registered commands")
                return is_command

        if muc:
            disabled_plugins = self.bot.mucs.get(muc, {}).get("disabled_plugins", [])
            whitelist_plugins = self.bot.mucs.get(muc, {}).get("whitelist_plugins")
            disabled_commands = self.bot.mucs.get(muc, {}).get("disabled_commands", [])
            if cmd_name in disabled_commands:
                logger.debug(f"Command '{cmd_name}' is disabled for {muc}")
                return is_command
            if self.commands[cmd_name].plugin in disabled_plugins:
                logger.debug(f"Plugin '{self.commands[cmd_name].plugin}' is disabled for {muc}, command '{cmd_name}' cannot be executed")
                return is_command
            if whitelist_plugins is not None and self.commands[cmd_name].plugin not in whitelist_plugins:
                logger.debug(f"Plugin '{self.commands[cmd_name].plugin}' is disabled for {muc}, command '{cmd_name}' cannot be executed")
                return is_command

        is_admin = True if jid in self.bot.admins else False
        is_admin_cmd = self.commands[cmd_name].admin
        logger.debug(f"Command '{cmd_name}' is_admin_cmd={is_admin_cmd}, user is_admin={is_admin}")

        if (is_admin_cmd and is_admin) or not is_admin_cmd:
            logger.info(f"Executing command '{body}' for user '{jid or nick}'")
            command_params = {"bot": self.bot, "msg":msg, "muc":muc, "nick":nick, "jid":jid, "quote":quote}
            await self.commands[cmd_name].call(command_params)
            is_command = True

        return is_command

    async def _send_suggested_command(self, cmd_name, muc, nick, jid):
        if len(cmd_name) > 15:
            logger.debug(f'Skipping command suggestion for long input: {cmd_name}')
            return None
        if len(cmd_name) < 5:
            logger.debug(f'Skipping command suggestion for short input: {cmd_name}')
            return None

        user = f"{muc}/{nick}" if muc else jid
        is_admin = True if jid in self.bot.admins else False

        best_suggestion = None
        min_distance = float('inf')

        for suggested_command in self.commands:
            is_admin_cmd = self.commands[suggested_command].admin
            if (is_admin_cmd and is_admin) or not is_admin_cmd:
                distance = self.levenshtein_distance(cmd_name, suggested_command)
                if distance < min_distance:
                    min_distance = distance
                    best_suggestion = suggested_command

        if min_distance >= len(cmd_name) or min_distance > 2:
            return None

        if self.run_command_suggestion:
            return best_suggestion
        else:
            logger.debug(f"Sending '{best_suggestion}' as suggested command to user '{user}' for command '{cmd_name}'")
            body = f"'{cmd_name}' is not a valid command, did you mean '{best_suggestion}'?"
            await self.bot.send_message_processed(mto=user, mbody=body, mtype="chat")
            return None

    # def _hamming_distance(self, s1, s2, pad_char='#'):
    #     max_len = max(len(s1), len(s2))
    #     s1_padded = s1.ljust(max_len, pad_char)
    #     s2_padded = s2.ljust(max_len, pad_char)

    #     return sum(c1 != c2 for c1, c2 in zip(s1_padded, s2_padded))

    # TODO: put in utils.py or something mayb

    def levenshtein_distance(self, s1, s2):
        if len(s1) < len(s2):
            return self.levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        return previous_row[-1]
