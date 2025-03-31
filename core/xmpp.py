import re
import os
import sys
import time
import json
import slixmpp
import pkgutil
import plugins
import logging
import asyncio
import aiohttp
import aiofiles
import importlib
import traceback
import urllib.parse
from core.sql import SQLHandler
from core.llm import ChatOrchestrator
from core.commands import CommandHandler

logger = logging.getLogger(__name__)

class Bot(slixmpp.ClientXMPP):
    def __init__(self, config):
        logger.info(f'{config.get("bot_nick", "Nokotan")}: "Waking up..."')
        super().__init__(config.get("bot_jid"), config.get("bot_pw"))
        self.config = config

        self.nick = config.get("bot_nick", "Nokotan")
        self.admins = config.get("admins", [])
        self.mucs = config.get("mucs", {})
        self.muc_timeout = config.get("muc_timeout", 30)
        self.ignored = config.get("ignored", [])
        self.max_history = config.get("max_history", 100)
        self.disabled_plugins = config.get("global_disabled_plugins", ["example, debug_plugin"])
        self.default_command_cooldown = config.get("default_command_cooldown", 2)
        self.default_prefix = config.get("default_command_prefix", ".")
        self.show_command_suggestion = config.get("show_command_suggestion", True)
        self.run_command_suggestion = config.get("run_command_suggestion", True)
        self.plugin_config = config.get("plugin_config", {})
        self.gateway_patterns = self._load_gateway_patterns()
        self.store_oob_links = config.get("store_oob_links", False)
        self.store_oob_files = config.get("store_oob_files", False)
        self.store_oob_exts = config.get("store_oob_exts", [])
        self.source_url = config.get("source_url", "https://github.com/Peacockli/nokotan")

        self.sql = SQLHandler(config.get("sql_file", "bot.db"))
        self.command_handler = CommandHandler(self)

        self.llm_config = config.get("llm", {})
        self.llm_enabled = self.llm_config.get("enabled", False)
        if self.llm_enabled:
            self.ollama_config = config.get("ollama", {})
            self.openai_config = config.get("openai", {})
            self.llm = ChatOrchestrator(
                openai_api_key=self.openai_config.get("api_key"),
                openai_host=self.openai_config.get("host"),
                openai_model=self.openai_config.get("model"),
                ollama_host=self.ollama_config.get("host"),
                ollama_model=self.ollama_config.get("model"),
                llm_temperature=self.llm_config.get("temperature", 1.2),
                llm_num_predict=self.llm_config.get("num_predict", 256),
            )

        self.joined_mucs = []
        self.user_states = self._load_user_states()
        self.plugins = {}

        self._register_xmpp_plugins()
        self._add_event_handlers()

    def _register_xmpp_plugins(self):
        self.plugin_whitelist = [
            'xep_0045', # MUC
            'xep_0066', # OOB
            'xep_0308', # Message Correction
            'xep_0359', # Unique and Stable Stanza IDs
            'xep_0363', # HTTP File Upload
            'xep_0425', # Message Moderation
            'xep_0444', # Message Reactions
            'xep_0461', # Message Reply
        ]
        self.register_plugins()

    def _add_event_handlers(self):
        event_handler_map = {
            "session_start": self.start_session,
            "groupchat_message": self.handle_groupchat_message,
            "message": self.handle_private_message,
            "groupchat_presence": self.handle_groupchat_presence,
            "reactions": self.handle_reactions,
            "disconnected": self.handle_disconnect,
            "connection_failed": self.handle_disconnect,
        }

        for event, handler in event_handler_map.items():
            self.add_event_handler(event, handler)

    def _load_plugins(self):
        logger.info("Starting plugin loading process.")

        for _, plugin_module, _ in pkgutil.iter_modules(plugins.__path__):
            if plugin_module in self.disabled_plugins or plugin_module == "base_plugin":
                logger.debug(f"Skipping disabled plugin: {plugin_module}")
                continue

            try:
                logger.debug(f"Attempting to load plugin: {plugin_module}")
                module = importlib.import_module(f'plugins.{plugin_module}')
                class_name = ''.join(word.capitalize() for word in plugin_module.split('_'))

                if hasattr(module, class_name):
                    plugin_class = getattr(module, class_name)
                    self.plugins[plugin_module] = plugin_class(self)
                    logger.info(f"Successfully loaded and initialized plugin: {plugin_module}")
                else:
                    logger.error(f"Error: Plugin {plugin_module} does not define a class with the same name.")
            except ImportError as e:
                logger.error(f"Error loading plugin module {plugin_module}: {e}\n{traceback.format_exc()}")

        logger.info("Plugin loading process completed.")

    def _load_gateway_patterns(self):
        json_path = "data/gateway_patterns.json"
        try:
            with open(json_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.debug(f"No gateway pattern file found at {json_path}. Initializing with empty dict.")
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w") as f:
                json.dump({}, f)
            return {}

    def _greet(self):
        hour = time.localtime().tm_hour
        if hour < 4:
            time_of_day = "Nya.. Why did u wake me this late..."
        if hour < 6:
            time_of_day = "Nya.. Why did u wake me this early..."
        elif hour < 12:
            time_of_day = "Good morning uwu"
        elif hour < 18:
            time_of_day = "Good afternoon :3"
        else:
            time_of_day = "Good evening owo"
        logger.info(f'{self.nick}: "{time_of_day}"')

    async def start_session(self, event):
        self.send_presence()
        for muc, config in self.mucs.items():
            await self.join_muc(muc, config.get("nick", self.nick))
        self._load_plugins()
        self._load_ignores()
        self._greet()

    async def join_muc(self, muc, nick):
        logger.info(f"Attempting to join MUC: {muc} with nick: {nick}")

        try:
            join_response = await self._join_muc_with_plugin(muc, nick)
            logger.debug(f"Successfully joined MUC: {muc}")

            await self._process_chat_history(muc, join_response[3])

            self._add_muc_to_joined_list(muc)

            logger.info(f"Successfully loaded chat history for MUC: {muc}")

        except Exception as e:
            logger.error(f"Error joining MUC {muc}: {e}")

    async def _join_muc_with_plugin(self, muc, nick):
        try:
            return await self.plugin['xep_0045'].join_muc_wait(muc, nick, maxstanzas=self.max_history, timeout=self.muc_timeout)
        except asyncio.TimeoutError as e:
            raise e

    def _add_muc_to_joined_list(self, muc):
        if muc not in self.joined_mucs:
            self.joined_mucs.append(muc)
            logger.info(f"Added MUC {muc} to joined_mucs list")

    async def _process_chat_history(self, muc, history):
        for message in history:
            stored_message = self.get_history_message(muc, message['id'])
            try:
                nick = str(message['from']).split('/')[-1]
                body = message['body']
                message_id = message['id']
                replace_id = message['replace'].get('id')
                stanza_id = message['stanza_id'].get('id')
                if stored_message is None:
                    logger.debug(f"message [{message['body']}] not in db, adding.")
                    await self._store_chat_history(muc, nick, message_id, stanza_id, body)
                if replace_id is not None:
                    logger.debug(f"(re)adding message correction [{message['body']}] to db.")
                    self._store_correction_history(muc, nick, message_id, replace_id, stanza_id, body)
            except KeyError as e:
                logger.error(f"Error processing message in history for MUC {muc}: {e}\n{traceback.format_exc()}")

    async def handle_groupchat_presence(self, presence):
        try:
            from_parts = str(presence["from"]).split("/")
            muc = from_parts[0]
            nick = from_parts[1]
            newnick = presence['muc']['nick'] if presence['muc']['nick'] != nick else None
            jid = presence['muc']['jid'].bare if presence['muc'].get('jid') else None

            logger.debug(f"Processing presence update for MUC: {muc}, nick: {nick}, newnick: {newnick}, jid: {jid}")

            if jid == self.boundjid.bare:
                logger.debug("Presence is from self, checking if we disconnected.")
                if presence["type"] == "unavailable":
                    logger.info(f"Bot got disconnected from {muc}.")
                    if muc in self.joined_mucs:
                        logger.debug(f"Removing {muc} from joined_mucs.")
                        self.joined_mucs.remove(muc)
                    if self.mucs.get(muc, {}).get("auto_rejoin", False):
                        logger.info(f"Attempting to rejoin {muc}...")
                        await self.join_muc(muc, self.mucs.get('nick', self.nick))
                return

            user, jid_visible = (jid, True) if jid else (nick, False)
            logger.debug(f"User identified as: {user}, JID is {'' if jid_visible else 'not'} visible.")

            disabled_plugins = self.mucs.get(muc, {}).get("disabled_plugins", [])
            logger.debug(f"Disabled plugins for MUC {muc}: {disabled_plugins}")

            if muc not in self.user_states:
                self.user_states[muc] = {}
            user_state = self.user_states[muc].get(user, {})

            if newnick and not jid_visible:
                logger.info(f"User {user} is changing nickname to {newnick}")
                if user in self.user_states[muc]:
                    self.user_states[muc][newnick] = self.user_states[muc].pop(user)
                    self.sql.delete(plugin_name="user_states", key=user)
                    role = presence["muc"]["role"]
                    affiliation = presence["muc"]["affiliation"]
                    status = presence["type"]
                    self._save_user_state(muc, user, role=role, affiliation=affiliation, status=status)
                user = newnick

            if user not in self.user_states[muc]:
                logger.info(f"New user {user} detected in MUC {muc}")
                role = presence["muc"]["role"]
                affiliation = presence["muc"]["affiliation"]
                status = presence["type"]
                first_seen = int(time.time())

                self.user_states[muc][user] = {
                    "role": role,
                    "affiliation": affiliation,
                    "status": status,
                    "first_seen": first_seen
                }
                self._save_user_state(muc, user, role=role, affiliation=affiliation, status=status, first_seen=first_seen)
                await self._handle_room_join(muc, user, presence, disabled_plugins)
            else:
                await self._handle_state_changes(muc, user, user_state, presence, disabled_plugins)

        except Exception as e:
            logger.error(f"Error handling presence: '{e}'\n{traceback.format_exc()}")

    async def _handle_state_changes(self, muc, user, user_state, presence, disabled_plugins):
        if presence["muc"]["role"] != user_state["role"]:
            logger.debug(f"Role change detected for user {user} in MUC {muc}: {user_state['role']} -> {presence['muc']['role']}")
            await self._handle_role_change(muc, user, presence, disabled_plugins)
            role = presence["muc"]["role"]
            self.user_states["role"] = role
            self._save_user_state(muc, user, role=role)

        if presence["muc"]["affiliation"] != user_state["affiliation"]:
            logger.debug(f"Affiliation change detected for user {user} in MUC {muc}: {user_state['affiliation']} -> {presence['muc']['affiliation']}")
            await self._handle_affiliation_change(muc, user, presence, disabled_plugins)
            affiliation = presence["muc"]["affiliation"]
            self.user_states["affiliation"] = affiliation
            self._save_user_state(muc, user, affiliation=affiliation)

        if presence["type"] != user_state["status"]:
            logger.debug(f"Status change detected for user {user} in MUC {muc}: {user_state['status']} -> {presence['type']}")
            await self._handle_status_change(muc, user, presence, disabled_plugins)
            status = presence["type"]
            self.user_states["status"] = status
            self._save_user_state(muc, user, status=status)

    async def _handle_presence_event(self, event_type, muc, user, presence, disabled_plugins):
        event_handler_map = {
            'room_join': 'handle_room_join',
            'role_change': 'handle_role_change',
            'affiliation_change': 'handle_affiliation_change',
            'status_change': 'handle_status_change',
        }
        handler_method = event_handler_map.get(event_type)
        if not handler_method:
            logger.error(f"Unknown event type: {event_type}")
            return

        logger.debug(f"Handling {event_type} for user {user} in MUC {muc}")
        for plugin in self.plugins.values():
            if plugin in disabled_plugins:
                logger.debug(f"Skipping disabled plugin {plugin.__class__.__name__} for {event_type}")
                continue
            if hasattr(plugin, handler_method):
                logger.debug(f"Calling {handler_method} for plugin {plugin.__class__.__name__}")
                try:
                    await getattr(plugin, handler_method)(muc, user, presence)
                except Exception as e:
                    logger.error(f"Error in plugin {plugin.__class__.__name__} during {handler_method}: {e}\n{traceback.format_exc()}")

    async def _handle_room_join(self, muc, user, presence, disabled_plugins):
        await self._handle_presence_event('room_join', muc, user, presence, disabled_plugins)

    async def _handle_role_change(self, muc, user, presence, disabled_plugins):
        await self._handle_presence_event('role_change', muc, user, presence, disabled_plugins)

    async def _handle_affiliation_change(self, muc, user, presence, disabled_plugins):
        await self._handle_presence_event('affiliation_change', muc, user, presence, disabled_plugins)

    async def _handle_status_change(self, muc, user, presence, disabled_plugins):
        await self._handle_presence_event('status_change', muc, user, presence, disabled_plugins)

    async def handle_groupchat_message(self, msg):
        logger.debug(f"Processing group chat message: {msg}")

        if self._should_ignore_groupchat_message(msg):
            return

        if msg['oob'].get('url'):
            logger.debug(f"Handling OOB file transfer from: {msg['from'].bare}")
            await self.handle_file_transfer(msg)
            return

        msg, quote = self._strip_reply_and_quote_from_msg(msg)
        await self._store_chat_history(msg['mucroom'], msg['mucnick'], msg['id'], msg['stanza_id'].get('id'), msg['body'])

        if msg['mucnick'] in self.gateway_patterns:
            logger.debug(f'gateway message received from {msg['mucnick']}, extracing nick and body')
            gateway_nick, gateway_body = self._extract_gateway_nick_body(msg['mucnick'], msg['body'])
            if gateway_nick and gateway_body:
                logger.debug(f'succesfully got body {gateway_body} from gateway, setting...')
                #msg['mucnick'] = gateway_nick # read only, can't set it.
                replace = self.gateway_patterns[msg['mucnick']].get('replace')
                if replace:
                    for replace_string, replace_with in replace.items():
                        gateway_body = gateway_body.replace(replace_string, replace_with)
                msg['body'] = gateway_body

        is_command = await self.command_handler.handle_groupchat_message(msg, quote)
        plugin_params = {"msg":msg, "quote":quote, "is_command":is_command}
        await self._send_message_to_plugins(plugin_params, "groupchat_message")

    async def handle_private_message(self, msg):
        logger.debug(f"Processing private message: {msg}")

        if self._should_ignore_private_message(msg):
            return

        if msg['from'].bare in self.joined_mucs:
            logger.debug(f"Handling MUC whisper from: {msg['from'].bare}")
            await self.handle_muc_whisper(msg)
            return

        jid = msg['from'].bare
        if jid in self.admins:
            logger.debug(f"Handling command from admin {jid}")
            try:
                is_command = await self.command_handler.handle_private_message(msg)
            except Exception as e:
                logger.error(f"Error handling private message from admin {jid}: {e}\n{traceback.format_exc()}")

    async def handle_muc_whisper(self, msg):
        logger.debug(f"Handling MUC whisper: {msg}")
        is_command = await self.command_handler.handle_whisper(msg)
        plugin_params = {"msg":msg, "is_command":is_command}
        await self._send_message_to_plugins(plugin_params, "whisper")

    async def handle_file_transfer(self, msg):
        if self.store_oob_links or self.store_oob_files:
            url = msg['oob'].get('url')
            ext = url.split('.')[-1]
            if ext in self.store_oob_exts or '*' in self.store_oob_exts:
                muc = msg.get('mucroom')
                nick = msg.get('mucnick')
                mid = msg.get('id')
                if self.store_oob_links:
                    await self._store_oob_urls(url, ext, muc, mid, nick)
                if self.store_oob_files:
                    await self._store_oob_files(url, ext, muc, mid, nick)
        logger.debug(f"Handling OOB file transfer: {msg}")
        await self._send_message_to_plugins({"msg":msg}, "file_transfer")

    async def handle_reactions(self, msg):
        logger.debug(f'Handling reaction: {msg}')
        if self._should_ignore_groupchat_message:
            return
        await self._send_message_to_plugins({"msg":msg}, "reaction")

    def send_reactions(self, reaction, msg=None, mto=None, mtype=None, to_id=None,):
        if msg is not None:
            mto=msg['from'].bare # won't work in whispers now
            if mto in self.joined_mucs and msg['type'] == 'chat':
                mto=msg['from']
            mtype=msg['type']
            to_id=msg['stanza_id'].get('id') if mto in self.joined_mucs else msg['id']

        if isinstance(reaction, str):
            reaction = {reaction}
        elif not isinstance(reaction, set):
            logging.error(f"Invalid type for reaction: {type(reaction)}. Expected a string or a set.")
            return

        # Fix gajim memes
        cleaned_reaction = set()
        for emoji in reaction:
            if '\uFE0F' in emoji:
                emoji = emoji.replace('\uFE0F', '')
                cleaned_reaction.add(emoji)
        if len(cleaned_reaction) > 0:
            reaction = cleaned_reaction

        try:
            msg = self.make_message(mto=mto, mtype=mtype)
            msg['reactions']['values'] = reaction
            msg['reactions']['id'] = to_id
            msg.send()
            logger.debug(f"Reaction '{reaction}' sent successfully to {mto}.")
        except Exception as e:
            logger.error(f"Error sending reaction: {e}\n{traceback.format_exc()}")

    def _extract_gateway_nick_body(self, gateway, message):
        regex = self.gateway_patterns.get(gateway).get("regex")
        if not regex:
            pattern = self.gateway_patterns[gateway].get('pattern')
            pattern_regex = re.escape(pattern)
            pattern_regex = pattern_regex.replace("nick", r"(?P<nick>.+?)")
            pattern_regex = pattern_regex.replace("body", r"(?P<body>.+?)$")

            regex = re.compile(pattern_regex)
            self.gateway_patterns.get(gateway)['regex'] = regex

        match = regex.match(message)

        if match:
            return match.group('nick'), match.group('body')
        else:
            logger.warn(f"{gateway} failed to match regex pattern.")
            return None, None

    def _should_ignore_groupchat_message(self, msg):
        ignore_conditions = [
            (msg['mucroom'] not in self.joined_mucs, f"Ignoring message from unjoined MUC room: {msg['mucroom']}"),
            (msg["mucnick"] in self.ignored, f"Ignoring message from ignored user: {msg['mucnick']}"),
            (self._is_encrypted(msg), "Ignoring OMEMO encrypted message"),
            (self._handle_correction(msg, True), "Ignoring message correction"),
            (self._is_self(msg), "Ignoring self-sent message"),
        ]
        for condition, log_message in ignore_conditions:
            if condition:
                logger.debug(log_message)
                return True
        return False

    def _should_ignore_private_message(self, msg):
        ignore_conditions = [
            (msg['type'] != 'chat', "Ignoring non-chat type message"),
            (self._is_encrypted(msg), "Ignoring OMEMO encrypted message"),
            (msg['oob'].get('url'), "Ignoring message with OOB URL"),
            (self._handle_correction(msg, False), "Ignoring message correction"),
        ]
        for condition, log_message in ignore_conditions:
            if condition:
                logger.debug(log_message)
                return True
        return False

    def _is_encrypted(self, msg):
        if msg.xml.find('{urn:xmpp:eme:0}encryption') is not None:
            encryption = msg.xml.find('{urn:xmpp:eme:0}encryption')
            return True
        return False

    def _handle_correction(self, msg, is_muc):
        if msg['replace'].get('id'):
            if is_muc:
                muc = msg['mucroom']
                nick = msg['mucnick']
                message_id = msg['id']
                replace_id = msg['replace'].get('id')
                stanza_id = msg['stanza_id'].get('id')
                body = msg['body']

                self._store_correction_history(muc, nick, message_id, replace_id, stanza_id, body)
            return True
        return False

    def _is_self(self, msg):
        muc = msg['from'].bare
        nick = self.mucs.get(muc, {}).get("nick", self.nick)
        if msg['mucnick'] == nick:
            return True
        return False

    def _strip_reply_and_quote_from_msg(self, msg):
        quote = None
        if msg['reply']['id']:
            msg['body'], quote = self._strip_reply_from_body(msg)
            if quote is None:
                return msg, quote
            lines = quote.splitlines()
            cleaned_lines = [line[1:].strip() for line in lines]
            quote ="\n".join(cleaned_lines)
        elif msg['body'].startswith(">"):
            lines = msg['body'].splitlines()
            filtered_lines = [line for line in lines if not line.startswith(">")]
            quote_lines = [line[1:] for line in lines if line.startswith(">")]
            msg['body'] = "\n".join(filtered_lines)
            quote = "\n".join(quote_lines)
        return msg, quote

    def _strip_reply_from_body(self, msg):
        for fallback in msg["fallbacks"]:
            if fallback["for"] == "urn:xmpp:reply:0":
                break
        else:
            logger.debug(f"No quote fallback found in:\n{msg}") # matterbridge moment
            return msg["body"], None

        start = fallback["body"]["start"]
        end = fallback["body"]["end"]
        body = msg["body"]

        if 0 <= start < end <= len(body):
            return body[:start] + body[end:], body[start:end]
        else:
            return body, None

    async def _send_message_to_plugins(self, plugin_params, message_type):
        msg = plugin_params['msg']
        logger.debug(f"Forwarding {message_type} message to plugins: {msg}")

        muc = msg["from"].bare
        disabled_plugins = self._get_disabled_plugins_for_muc(muc)
        whitelist_plugins = self._get_whitelist_plugins_for_muc(muc)

        start = time.perf_counter()
        send_count = 0
        for plugin in self.plugins.values():
            plugin_name = plugin.__class__.__module__.split('.')[-1]
            if (plugin_name in disabled_plugins) or (whitelist_plugins is not None and plugin_name not in whitelist_plugins):
                logger.debug(f"Skipping disabled plugin: {plugin.__class__.__name__}")
                continue
            handler_method = f"handle_{message_type}"
            if hasattr(plugin, handler_method):
                send_count += 1
                logger.debug(f"Forwarding message to plugin: {plugin.__class__.__name__}")
                try:
                    plugin_start = time.perf_counter()
                    await getattr(plugin, handler_method)(plugin_params)
                    plugin_end = time.perf_counter()
                    plugin_time = (plugin_end - plugin_start) * 1e6
                    logger.debug(f"Plugin {plugin.__class__.__name__} took {plugin_time:.2f} microseconds to handle {message_type} message.")
                except Exception as e:
                    logger.error(f"Error in plugin {plugin.__class__.__name__} during {handler_method}: {e}\n{traceback.format_exc()}")
            else:
                logger.debug(f"Plugin {plugin.__class__.__name__} does not handle {message_type} messages")
        end = time.perf_counter()
        total_time = (end - start) * 1e3
        logger.debug(f"Took {total_time:.2f} milliseconds to send message from {muc} to {send_count} {message_type} handler(s).")

    async def _process_mbody_before_sending(self, mto, mbody):
        if type(mto) == slixmpp.jid.JID:
            mto = mto.bare


        if mto in self.joined_mucs:
            for muc, config in self.mucs.items():
                if mto == muc:
                    silent_mode = config.get("silent_mode")
                    if silent_mode:
                        return None

                    filter_muc = config.get("llm_filter_all_msgs")
                    filter_all = self.llm_config.get("filter_all_msgs")
                    if filter_muc:
                        prompt = config.get("llm_filter_prompt")
                        if prompt:
                            mbody = await self.llm.send_prompt(prompt, {"text": mbody})
                    elif filter_all:
                        prompt = self.llm_config.get("filter_prompt")
                        if prompt:
                            mbody = await self.llm.send_prompt(prompt, {"text": mbody})

                    allow_mentions = config.get("allow_mentions", True)
                    if not allow_mentions:
                        zero_width = "\u200B"
                        roster = await self.get_users(mto)
                        for user in roster:
                            if user in mbody:
                                mbody = mbody.replace(user, f"{user[0]}{zero_width}{user[1:]}")

        return mbody

    async def send_message_processed(self, mbody, msg=None, mto=None, mtype=None, reply_id=None):
        if msg:
            jid = msg['from']
            mtype=msg['type']
            if jid.bare in self.joined_mucs and mtype == 'chat':
                mto=jid
            else:
                mto=jid.bare
        mbody = await self._process_mbody_before_sending(mto, mbody)
        if mbody:
            msg = self.Message()
            msg['to'] = mto
            msg['body'] = mbody
            msg['type'] = mtype
            if reply_id:
                msg['reply']['id'] = reply_id
            msg.send()

    async def send_file(self, file_name, input_file, mto, mtype="groupchat", oob=True):
        try:
            if len(file_name) > 255:
                filename_parts = file_name.split('.')
                if len(filename_parts) == 1:
                    file_name = file_name[:255]
                else:
                    filename_base = '.'.join(filename_parts[:-1])
                    filename_ext = '.' + filename_parts[-1]
                    if len(filename_ext) > 255:
                        raise ValueError("File extension is too long.")
                    file_name = filename_base[:255 - len(filename_ext)] + filename_ext
            logger.debug(f"Attempting to upload file: {file_name}")
            url = await self['xep_0363'].upload_file(file_name, input_file=input_file)
            logger.info(f"File {file_name} uploaded successfully. URL: {url}")
            message = self.make_message(mto=mto, mbody=f"{url}", mtype=mtype)
            if oob:
                message['oob']['url'] = url
            message.send()
            ext = url.split('.')[-1]
            filename = url.split('/')[-1].split('.')[0][:20]
            await self._store_oob_files(url, ext, mto, filename, self.nick)
            logger.info(f"Sent download link for {file_name} to {mto}")
        except Exception as e:
            logger.error(f"Failed to upload file {file_name}: {e}")

    def edit_message(self, mto, mid, mbody, mtype):
        try:
            msg = self.Message()
            msg['to'] = mto
            msg['replace']['id'] = mid
            msg['body'] = mbody
            msg['type'] = mtype
            msg.send()

            logger.debug(f"Successfully edited message with ID '{mid}' to '{mto}'. New body: '{mbody}'.")

        except Exception as e:
            logger.error(f"Error editing message with ID '{mid}' to '{mto}': {e}\n{traceback.format_exc()}")
            raise

    async def moderate_message(self, muc, mid, reason="Spam"):
        try:

            await self.plugin['xep_0425'].moderate(slixmpp.JID(muc), mid, reason)

            logger.debug(f"Succesfully moderated message with ID '{mid}' in '{muc}' for '{reason}'.")

        except Exception as e:
            logger.error(f"Error moderating message with ID '{mid}' in '{muc}': {e}\n{traceback.format_exc()}")
            raise

    def _get_disabled_plugins_for_muc(self, muc):
        return self.mucs.get(muc, {}).get("disabled=_plugins", [])

    def _get_whitelist_plugins_for_muc(self, muc):
        return self.mucs.get(muc, {}).get("whitelist_plugins")

    def get_history_message(self, muc, message_id):
        if self.sql.get("bot_chat_history", f"{muc}_{message_id}", "timestamp") is None:
            return None
        fields = self.sql.get_all_fields("bot_chat_history", f"{muc}_{message_id}")
        history_message = {}
        for field in fields:
            history_message[field] = self.sql.get("bot_chat_history", f"{muc}_{message_id}", field)
        return history_message

    def get_x_messages(self, muc, num_messages=1, descending=True, start=0, nick=None, jid=None):
        if start < 0:
            logger.warn("Can't use negative values with self.get_x_messages, using absolute value.")
            abs(start)

        if jid is not None:
            filter_field, filter_value = "jid", jid
        elif nick is not None:
            filter_field, filter_value = "nick", nick
        else:
            filter_field, filter_value = None, None

        ordered_messages = self.sql.get_ordered_by(
            "bot_chat_history",
            "timestamp",
            limit=num_messages,
            offset=start,
            descending=descending,
            key_pattern=f"{muc}_%",
            filter_field=filter_field,
            filter_value=filter_value
        )
        if len(ordered_messages) == 0:
            return None
        return ordered_messages if num_messages > 1 else ordered_messages[0]

    def get_x_oob_urls(self, muc=None, num_messages=1, descending=True, start=0, nick=None, jid=None):
        if start < 0:
            logger.warn("Can't use negative values with self.get_x_oob_urls, using absolute value.")
            abs(start)

        if jid is not None:
            filter_field, filter_value = "jid", jid
        elif nick is not None:
            filter_field, filter_value = "nick", nick
        else:
            filter_field, filter_value = None, None

        ordered_messages = self.sql.get_ordered_by(
            "bot_oob_history",
            "timestamp",
            limit=num_messages,
            offset=start,
            descending=descending,
            key_pattern=f"{muc}_%" if muc else None,
            filter_field=filter_field,
            filter_value=filter_value
        )
        if len(ordered_messages) == 0:
            return None
        return ordered_messages if num_messages > 1 else ordered_messages[0]

    async def _store_oob_files(self, url, ext, muc, mid, nick):
        store_directory = f'./data/oob_downloads/{muc}'
        ext = url.split('.')[-1].lower()

        image_exts = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tiff", "ico"]
        video_exts = ["webm", "mp4", "ogv", "mov", "avi", "mkv", "flv", "wmv"]
        audio_exts = ["mp3", "wav", "ogg", "flac", "aac", "m4a", "wma"]
        doc_exts = ["pdf", "csv", "txt", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "rtf"]
        archive_exts = ["zip", "rar", "tar", "gz", "7z", "bz2"]
        code_exts = ["py", "js", "java", "c", "cpp", "h", "html", "css", "php", "rb", "sh", "rs", "go"]

        if ext in image_exts:
            store_directory = os.path.join(store_directory, "images")
        elif ext in video_exts:
            store_directory = os.path.join(store_directory, "videos")
        elif ext in audio_exts:
            store_directory = os.path.join(store_directory, "audio")
        elif ext in doc_exts:
            store_directory = os.path.join(store_directory, "docs")
        elif ext in archive_exts:
            store_directory = os.path.join(store_directory, "archives")
        elif ext in code_exts:
            store_directory = os.path.join(store_directory, "code")
        else:
            store_directory = os.path.join(store_directory, "other")

        os.makedirs(store_directory, exist_ok=True)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    timestamp = int(time.time())
                    encoded_nick = urllib.parse.quote(nick)[:100]
                    unique_filename = f"{mid}_{timestamp}_{encoded_nick}.{ext}"
                    filepath = os.path.join(store_directory, unique_filename)

                    with open(filepath, 'wb') as f:
                        f.write(await response.read())
                    logger.debug(f"File saved successfully: {filepath}")
        except aiohttp.ClientError as e:
            logger.error(f"Error downloading {url}: {e}")

    async def delete_oob_files(self, muc, nick=None, start_time=None, end_time=None):
        muc_directory = os.path.join('./data/oob_downloads', muc)

        if not os.path.exists(muc_directory):
            logger.warning(f"MUC directory not found: {muc_directory}")
            return

        async def delete_file(file_path):
            try:
                await aiofiles.os.remove(file_path)
                logger.debug(f"File deleted successfully: {file_path}")
            except OSError as e:
                logger.error(f"Error deleting file {file_path}: {e}")

        async def process_directory(directory):
            for entry in os.scandir(directory):
                if entry.is_file():
                    file = entry.name
                    if nick is not None:
                        encoded_nick = urllib.parse.quote(nick)
                        if f"_{encoded_nick}." not in file:
                            continue

                    if start_time is not None or end_time is not None:
                        parts = file.split('_')
                        if len(parts) < 2:
                            continue
                        file_timestamp = int(parts[-2])

                        if start_time is not None and file_timestamp < start_time:
                            continue
                        if end_time is not None and file_timestamp > end_time:
                            continue

                    await delete_file(entry.path)
                elif entry.is_dir():
                    await process_directory(entry.path)

        await process_directory(muc_directory)

    async def _store_oob_urls(self, url, ext, muc, message_id, nick):
        logger.debug(f"Adding oob url to oob history: MUC={muc}, nick={nick}, url={url}")
        jid = await self.get_jid_from_nick(muc, nick)
        if jid is not None:
            self.sql.set("bot_oob_history", f"{muc}_{message_id}", "jid", jid)
        self.sql.set("bot_oob_history", f"{muc}_{message_id}", "nick", nick)
        self.sql.set("bot_oob_history", f"{muc}_{message_id}", "url", url)
        filename = os.path.splitext(url.split('/')[-1])[0]
        self.sql.set("bot_oob_history", f"{muc}_{message_id}", "filename", filename)
        self.sql.set("bot_oob_history", f"{muc}_{message_id}", "ext", ext)
        self.sql.set("bot_oob_history", f"{muc}_{message_id}", "timestamp", int(time.time()))

    async def _store_chat_history(self, muc, nick, message_id, stanza_id, body):
        logger.debug(f"Adding message to history: MUC={muc}, nick={nick}, id={message_id}")
        jid = await self.get_jid_from_nick(muc, nick)
        if jid is not None:
            self.sql.set("bot_chat_history", f"{muc}_{message_id}", "jid", jid)
        self.sql.set("bot_chat_history", f"{muc}_{message_id}", "nick", nick)
        self.sql.set("bot_chat_history", f"{muc}_{message_id}", "stanza_id", stanza_id)
        self.sql.set("bot_chat_history", f"{muc}_{message_id}", "body", body)
        self.sql.set("bot_chat_history", f"{muc}_{message_id}", "timestamp", int(time.time()))

    def _store_correction_history(self, muc, nick, message_id, replace_id, stanza_id, body):
        logger.debug(f"Correcting message in history: MUC={muc}, nick={nick}, id={replace_id}...")
        fields = [
            "body",
            "timestamp",
            "edit_timestamp",
            "edit_history"
        ]
        values = {}
        for field in fields:
            values[field] = self.sql.get("bot_chat_history", f"{muc}_{replace_id}", field)

        values["edit_history"] = json.loads(values["edit_history"] or "{}")

        history_key = values["edit_timestamp"] or values["timestamp"]
        values["edit_history"][history_key] = values["body"]

        self.sql.set("bot_chat_history", f"{muc}_{replace_id}", "body", body)
        self.sql.set("bot_chat_history", f"{muc}_{replace_id}", "edit_timestamp", int(time.time()))
        self.sql.set("bot_chat_history", f"{muc}_{replace_id}", "edit_history", json.dumps(values["edit_history"]))

    def _load_user_states(self):
        logger.debug("Loading user states from the database")

        user_states_raw = self.sql.get_all_keys("bot_user_states")
        user_states = {}

        for key, fields in user_states_raw.items():
            muc, user = key.split("_", 1)

            if muc not in user_states:
                user_states[muc] = {}

            user_states[muc][user] = {
                "role": fields.get("role", ""),
                "affiliation": fields.get("affiliation", ""),
                "status": fields.get("status", ""),
                "first_seen": fields.get("first_seen", "")
            }
            logger.debug(f"Loaded state for user {user} in MUC {muc}: {user_states[muc][user]}")

        logger.debug(f"Finished loading user states. Total MUCs: {len(user_states)}")
        return user_states

    def _save_user_state(self, muc, user, role=None, affiliation=None, status=None, first_seen=None):
        logger.debug(f"Saving user state for {user} in MUC {muc}")

        if role:
            self.sql.set("bot_user_states", f"{muc}_{user}", "role", role)
            logger.debug(f"Saved role for {user}: {role}")

        if affiliation:
            self.sql.set("bot_user_states", f"{muc}_{user}", "affiliation", affiliation)
            logger.debug(f"Saved affiliation for {user}: {affiliation}")

        if status:
            self.sql.set("bot_user_states", f"{muc}_{user}", "status", status)
            logger.debug(f"Saved status for {user}: {status}")

        if first_seen:
            if self.sql.get("bot_user_states",f"{muc}_{user}", "first_seen"):
                logger.warn(f"Blocked attempt to overwrite first_seen for {muc}_{user}. This shouldn't be happening.")
                return
            self.sql.set("bot_user_states", f"{muc}_{user}", "first_seen", first_seen)
            logger.debug(f"Saved first_seen for {user}: {time}")

        logger.debug(f"Finished saving user state for {user} in MUC {muc}")

    def add_ignore(self, nick, admin):
        if nick not in self.ignored:
            self.ignored.append(nick)
        self.sql.set("bot_ignore", nick, "admin", admin)

    def remove_ignore(self, nick):
        if nick in self.ignored:
            self.ignored.remove(nick)
        self.sql.delete("bot_ignore", nick, "admin")

    def get_ignore_admin(self, nick):
        return self.sql.get("bot_ignore", nick, "admin")

    def _load_ignores(self):
        ignores = self.sql.get_all_keys("bot_ignore")
        for nick, fields in ignores.items():
            if nick not in self.ignored:
                self.ignored.append(nick)

    def register_command(self, name, plugin, function, category=None, aliases=None, cooldown=None, hidden=False, admin=False):
        self.command_handler.register_command(name, plugin, function, category, aliases, cooldown, hidden, admin)

    async def set_user_role(self, muc, nick, role):
        moderator_list = await self.plugin['xep_0045'].get_roles_list(muc, 'moderator')
        if nick in moderator_list or not await self.is_bot_moderator(muc):
            return False

        try:
            await self.plugin['xep_0045'].set_role(muc, role=role, nick=nick)
            return True
        except slixmpp.exceptions.IqError as IqError:
            logger.info(f"Failed setting user role: {IqError.iq['error']['condition']}")
            return False

    async def set_user_affiliation(self, muc, nick, affiliation):
        moderator_list = await self.plugin['xep_0045'].get_roles_list(muc, 'moderator')
        if nick in moderator_list or not await self.is_bot_moderator(muc):
            return False

        try:
            await self.plugin['xep_0045'].set_affiliation(muc, affiliation, nick=nick)
            return True
        except slixmpp.exceptions.IqError as IqError:
            logger.info(f"Failed setting user affiliation: {IqError.iq['error']['condition']}")
            return False

    async def is_bot_moderator(self, muc):
        try:
            bot_nick = self.mucs.get(muc, {}).get("nick", self.nick)
            moderator_list = await self.plugin['xep_0045'].get_roles_list(muc, 'moderator')
            if bot_nick in moderator_list:
                return True
            return False
        except:
            return False

    async def get_jid_from_nick(self, muc, nick):
        try:
            roster = await self.get_users(muc)
            if nick not in roster:
                logger.warning(f"Nickname '{nick}' not found in MUC '{muc}'.")
                return None

            jid_with_client = self.plugin['xep_0045'].get_jid_property(muc, nick, 'jid')
            jid = jid_with_client.split("/")[0]
            logger.debug(f"Retrieved JID '{jid}' for nickname '{nick}' in MUC '{muc}'.")
            return jid

        except Exception as e:
            logger.error(f"Error retrieving JID for nickname '{nick}' in MUC '{muc}': {e}\n{traceback.format_exc()}")
            return None

    async def get_users(self, muc):
        return self.plugin['xep_0045'].get_roster(muc)

    def add_attribute(self, name, value):
        logger.debug(f"Attempting to add attribute '{name}'.")

        if hasattr(self, name):
            logger.error(f"Error, attribute '{name}' already exists.")
            return

        try:
            setattr(self, name, value)
            logger.info(f"Successfully added attribute '{name}' to the bot class.")
        except Exception as e:
            logger.error(f"Failed to add attribute '{name}': {e}\n{traceback.format_exc()}")

    async def _shutdown_or_restart(self, action="shutdown"):
        for plugin in self.plugins.values():
            if plugin in self.disabled_plugins:
                continue
            if hasattr(plugin, '_handle_shutdown'):
                await plugin._handle_shutdown()

        logger.info(f'{self.nick}: "Going to sleep..."' if action == "shutdown" else f'{self.nick}: "Taking a quick nap..."')
        self.disconnect()
        loop = asyncio.get_event_loop()
        loop.stop()

        if action == "shutdown":
            try:
                await asyncio.wait_for(asyncio.gather(*asyncio.all_tasks()), timeout=10)
            except asyncio.TimeoutError:
                logger.warn(f'"Graceful shutdown failed, forcing exit..."')
                sys.exit(0)
        elif action == "restart":
            os.execv(sys.executable, [sys.executable] + sys.argv)

    async def shutdown(self):
        await self._shutdown_or_restart(action="shutdown")

    async def restart(self):
        await self._shutdown_or_restart(action="restart")

    async def handle_disconnect(self, e):
        logger.warn(f"Disconnected, reason: {e}")
        logger.info("Restarting bot to reconnect in 15 seconds...")
        await asyncio.sleep(15)
        await self.restart()
