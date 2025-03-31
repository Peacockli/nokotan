import traceback
import logging
import asyncio
import time
import json
import re
import os

class BasePlugin:
    def __init__(self, bot):
        self.bot = bot
        self.plugin_name = self.__class__.__module__.split('.')[-1]

        self.config = self.bot.plugin_config.get(self.plugin_name, {})

        self._tasks = set()

        self.log = logging.getLogger(f"plugin.{self.plugin_name}")

        self.log.debug(f"Registering variables for plugin {self.plugin_name}.")
        self.register_variables()

        self.log.debug(f"Registering commands for plugin {self.plugin_name}.")
        self.register_commands()

        self.log.debug(f"Registering tasks for plugin {self.plugin_name}.")
        self.register_tasks()

    def create_task(self, coroutine, task_name=None):
        try:
            coroutine = coroutine()
            task = asyncio.create_task(coroutine)
            self._tasks.add(task)
            if task_name:
                task.set_name(task_name)
            task.add_done_callback(self._task_done_callback)
            self.log.debug(f"Created task: {task_name if task_name else coroutine.__name__}")

            return task
        except Exception as e:
            self.log.exception(f"Failed to create task {task_name if task_name else coroutine.__name__}: {e}")
            raise

    def create_periodic_task(self, coroutine, interval, task_name=None):
        async def wrapped_coroutine():
            try:
                while True:
                    await coroutine()
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                self.log.info(f"Periodic task {task_name if task_name else coroutine.__name__} was cancelled.")
                raise
        try:
            task = self.create_task(wrapped_coroutine, task_name=task_name)
            self._tasks.add(task)
            return task
        except Exception as e:
            self.log.exception(f"Failed to create periodic task {task_name if task_name else coroutine.__name__}: {e}")
            raise

    def create_scheduled_task(self, coroutine, timestamp, task_name=None):
        async def wrapped_coroutine():
            delay = timestamp - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            await coroutine()

        try:
            task = self.create_task(wrapped_coroutine, task_name=task_name)
            self._tasks.add(task)
            return task
        except Exception as e:
            self.log.exception(f"Failed to create scheduled task {task_name if task_name else coroutine.__name__}: {e}")
            raise

    def _task_done_callback(self, task):
        try:
            self._tasks.remove(task)
            if task.cancelled():
                self.log.debug(f"Task {task.get_name() if task.get_name() else task} was cancelled.")
            elif task.exception():
                self.log.exception(f"Task {task.get_name() if task.get_name() else task} raised an exception:", exc_info=task.exception())
            else:
                self.log.debug(f"Task {task.get_name() if task.get_name() else task} completed successfully.")
        except KeyError:
            self.log.warning(f"Task {task.get_name() if task.get_name() else task} not found in _tasks set.")
        except Exception as e:
            self.log.exception(f"Error in _task_done_callback: {e}")

    async def cancel_tasks(self, timeout=10):
        self.log.info(f"Cancelling {len(self._tasks)} tasks...")

        for task in self._tasks:
            if not task.done():
               task.cancel()
               self.log.debug(f"Cancelled task: {task.get_name() if task.get_name() else task}")

        if self._tasks:
            try:
                await asyncio.wait(self._tasks, timeout=timeout)

                for task in self._tasks:
                    if not task.done():
                         self.log.warning(f"Task {task.get_name() if task.get_name() else task} did not finish within the shutdown timeout.")


            except asyncio.TimeoutError:
                self.log.error("Timeout waiting for tasks to cancel during shutdown!")
            except Exception as e:
                self.log.exception(f"Error while cancelling tasks.")

        self._tasks.clear()
        self.log.info("Task cancellation complete.")

    async def get_extras_cmd(self, params):
        msg = params['msg']
        if msg['type'] == 'groupchat':
            muc = msg.get('mucroom', msg['from'].bare)
            nick = msg.get('mucnick', msg['from'].resource)
            jid =  await self.bot.get_jid_from_nick(muc, nick)
            roster = await self.bot.get_users(muc)
        else:
            if msg['from'].bare in self.bot.joined_mucs:
                muc = msg['from'].bare
                nick = msg['from'].resource
                jid =  await self.bot.get_jid_from_nick(muc, nick)
                roster = await self.bot.get_users(muc)
            else:
                muc = None
                nick = None
                jid = msg['from'].bare
                roster = None
        config = self.bot.mucs.get(muc, {})
        prefix = config.get("command_prefix", self.bot.default_prefix)

        extras = {'muc': muc, 'nick': nick, 'jid': jid, 'roster': roster, 'prefix': prefix}
        return extras

    async def get_extras_message(self, params):
        msg = params['msg']
        muc = msg.get('mucroom', msg['from'].bare)
        nick = msg.get('mucnick', msg['from'].resource)
        jid =  await self.bot.get_jid_from_nick(muc, nick)
        bot_is_mod = await self.bot.is_bot_moderator(muc)
        role = None
        affiliation = None
        if bot_is_mod:
            roles = ['moderator', 'participant', 'visitor']
            for role in roles:
                roles_list = await self.bot.plugin['xep_0045'].get_roles_list(muc, role)
                if nick in roles_list:
                    break
            affiliations = ['member', 'admin']
            for affiliation in affiliations:
                affiliations_list = await self.bot.plugin['xep_0045'].get_affiliation_list(muc, affiliation)
                if jid in affiliations_list:
                    break
        first_seen = self.bot.user_states[muc][jid]['first_seen'] if jid else self.bot.user_states[muc][nick]['first_seen']
        first_seen = int(first_seen)
        file = msg['oob'].get('url', None)
        config = self.bot.mucs.get(muc, {})
        prefix = config.get("command_prefix", self.bot.default_prefix)
        extras = {
            'muc': muc,
            'nick': nick,
            'jid': jid,
            'bot_is_mod': bot_is_mod,
            'role': role,
            'affiliation': affiliation,
            'first_seen': first_seen,
            'file': file,
            'prefix': prefix
        }
        return extras

    async def get_extras_presence(self, muc, user, presence):
        from_parts = str(presence["from"]).split("/")
        muc = from_parts[0]
        nick = from_parts[1]
        newnick = presence['muc']['nick'] if presence['muc']['nick'] != nick else None
        jid = presence['muc']['jid'].bare if presence['muc'].get('jid') else None
        jid_visible = True if jid else False

        pattern = r"@([^/]+)"
        if jid_visible:
            match = re.search(pattern, jid)
            if match:
                domain = match.group(1)
            else:
                domain = None

        role = presence["muc"]["role"]
        affiliation = presence["muc"]["affiliation"]
        status = presence["type"]

        is_bot_moderator = await self.bot.is_bot_moderator(muc)

        extras = {
            'nick': nick,
            'newnick': newnick,
            'jid': jid,
            'domain': domain,
            'jid_visible': jid_visible,
            'role': role,
            'affiliation': affiliation,
            'status': status,
            'is_bot_moderator': is_bot_moderator
        }
        return extras

    def get_data(self, file, mode, fallback=None):
        file_path = f"plugins/data/{self.plugin_name}/{file}"
        self.log.debug(f"Attempting to load file: {file_path} in mode: {mode}")

        try:
            with open(file_path, "r") as f:
                if mode == "json":
                    self.log.debug(f"Loading JSON data from {file_path}")
                    loaded_file = json.load(f)
                elif mode == "plaintext":
                    self.log.debug(f"Loading plaintext data from {file_path}")
                    loaded_file = f.read().splitlines()
                elif mode == "binary":
                    self.log.debug(f"Loading binary data from {file_path}")
                    loaded_file = f.read()
                else:
                    self.log.error(f"Unsupported mode: {mode}\n{traceback.format_exc()}")
                    loaded_file = None
        except FileNotFoundError:
            self.log.warn(f"File not found: {file_path}.")
            if fallback is None:
                self.log.info(f"Initializing {file_path} with empty file.")
                self.set_data(file, mode, "")
                loaded_file = self.get_data(file, mode)
            else:
                self.log.info(f"Saving provided fallback data '{fallback}' to {file_path}.")
                self.set_data(file, mode, fallback)
                loaded_file = self.get_data(file, mode)

        except json.JSONDecodeError as e:
            self.log.error(f"Error decoding JSON data: {e}\n{traceback.format_exc()}")
            loaded_file = None
        except Exception as e:
            self.log.error(f"Error loading data file: {e}\n{traceback.format_exc()}")
            loaded_file = None
        return loaded_file

    def set_data(self, file, mode, data):
        folder_path = f"plugins/data/{self.plugin_name}"
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            self.log.debug(f"Created directory: {folder_path}")

        file_path = os.path.join(folder_path, file)
        self.log.debug(f"Attempting to save file: {file_path} in mode: {mode}")

        try:
            with open(file_path, "w" if mode != "binary" else "wb") as f:
                if mode == "json":
                    self.log.debug(f"Saving JSON data to {file_path}")
                    json.dump(data, f, indent=4)
                elif mode == "plaintext":
                    self.log.debug(f"Saving plaintext data to {file_path}")
                    if isinstance(data, list):
                        f.write("\n".join(data))
                    else:
                        f.write(str(data))
                elif mode == "binary":
                    self.log.debug(f"Saving binary data to {file_path}")
                    f.write(data)
                else:
                    self.log.error(f"Unsupported mode: {mode}\n{traceback.format_exc()}")
                    return False
        except Exception as e:
            self.log.error(f"Error saving data to file: {e}\n{traceback.format_exc()}")
            return False
        return True

    async def _handle_shutdown(self):
         await self.handle_shutdown()
         await self.cancel_tasks()

    async def handle_shutdown(self):
        """
        Override this method to handle plugin-specific shutdown tasks.
        This is called *before* the generic task cancellation.
        """
        pass

    def register_variables(self):
        """Override this method to register variables"""
        pass

    def register_commands(self):
        """Override this method to register bot commands."""
        pass

    def register_tasks(self):
        """Override this method to register asyncio tasks."""
        pass

    async def handle_groupchat_message(self, params):
        """Override this method to handle plugin-specific groupchat message handling."""
        pass

    async def handle_whisper(self, params):
        """Override this method to handle plugin-specific whisper handling."""
        pass

    async def handle_file_transfer(self, params):
        """Override this method to handle plugin-specific OOB file transfer handling."""
        pass

    async def handle_reaction(self, params):
        """Override this method to handle plugin-specific reaction responses."""
        pass

    async def handle_room_join(self, muc, user, presence):
        """Override this method to handle plugin-specific room join handling."""
        pass

    async def handle_role_change(self, muc, user, presence):
        """Override this method to handle plugin-specific role change handling."""
        pass

    async def handle_status_change(self, muc, user, presence):
        """Override this method to handle plugin-specific status change handling."""
        pass
