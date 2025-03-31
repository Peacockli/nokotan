import asyncio
import traceback
import inspect
import re
from .base_plugin import BasePlugin

class DebugPlugin(BasePlugin):
    def __init__(self, bot):
        """
        ⚠️⚠️⚠️
        Use for debugging only, NOT safe.
        Put debug_plugin in global_disabled_plugins config.

        "global_disabled_plugins": [
            "debug_plugin"
        ]

        Or better yet delete this plugin file entirely if you don't need it.
        ⚠️⚠️⚠️
        """
        super().__init__(bot)
        self.notify_admins = self.config.get("notify_admins", True)
        self.pm_only = self.config.get("pm_only", True)
        self.current_from = None
        self.current_type = None

    def register_commands(self):
        self.bot.register_command('exec', self.plugin_name, self.exec_command, admin=True, category='admin')
        self.bot.register_command('echo', self.plugin_name, self.echo_command, admin=True, category='admin')
        self.bot.register_command('react', self.plugin_name, self.react_command, admin=True, category='admin')
        self.bot.register_command('roster', self.plugin_name, self.roster_command, admin=True, category='admin')
        self.bot.register_command('delete_user_states', self.plugin_name, self.delete_user_states, admin=True, category='admin')

    async def exec_command(self, params):
        msg = params['msg']
        muc = params['muc']
        nick = params['nick']
        jid = params['jid']
        quote = params['quote']
        """
        Admin command to execute Python code.
        Usage: 'exec <python_code>'
        Hint: Use self.reply(str) to output to chat.
        """
        self.current_from = msg['from'].bare
        self.current_type = msg['type']

        if muc and self.pm_only:
            return

        if muc:
            prefix = self.bot.mucs.get(muc, {}).get("command_prefix", self.bot.default_prefix)
        else:
            prefix = self.bot.default_prefix

        command = msg['body'].strip()
        code = command[len('exec')+len(prefix):].strip()

        self.log.warn(f"Received exec command with code:\n{code}")

        if self.notify_admins:
            for jid in self.bot.admins:
                body = f'{msg['from']} is running exec:\n{code}'
                await self.bot.send_message_processed(mto=jid, mbody=body, mtype='chat')

        if not code:
            message = "No code provided. Usage: 'exec <python_code>'"
            self.log.info("No code provided for exec command")

        else:
            try:
                exec(code)
                message = "Code executed successfully."
                self.log.info("Code executed successfully")
            except Exception as e:
                error_msg = traceback.format_exc()
                message = f"Code execution failed. Error: {error_msg}"
                self.log.info(f"Code execution failed: {error_msg}")

        await self.bot.send_message_processed(message, msg=msg)

    def reply(self, body):
        body = str(body)
        if len(body) > 1024:
            self.log.info(f"exec output:\n{body}")
            body_too_long_message = '...\n\nReply too long, see console for full output.'
            body = f'{body[:1024-len(body_too_long_message)]}{body_too_long_message}'
        if self.current_from is not None:
            asyncio.create_task(self._reply(body))

    async def _reply(self, body):
        await self.bot.send_message_processed(mto=self.current_from, mbody=body, mtype=self.current_type)

    async def echo_command(self, params):
        msg = params['msg']
        muc = params['muc']
        if muc:
            prefix = self.bot.mucs.get(muc, {}).get("command_prefix", self.bot.default_prefix)
        else:
            prefix = self.bot.default_prefix
        message = msg['body'][len('echo')+len(prefix):].strip()
        await self.bot.send_message_processed(message, msg=msg)

    async def react_command(self, params):
        parts = params['msg']['body'].split(' ')
        if len(parts) == 2:
            reaction = parts[1]
        elif len(parts) > 2:
            reaction = set(parts[1:])

        self.bot.send_reactions(msg=params['msg'], reaction=reaction)

    async def roster_command(self, params):
        parts = params['msg']['body'].split(' ')
        roster = await self.bot.get_users(parts[1])
        roster_string = ''
        for user in roster:
            roster_string += f"{user}, "
        await self.bot.send_message_processed(roster_string, msg=params['msg'])

    async def delete_user_states(self, params):
        msg = params['msg']
        extras = await self.get_extras_cmd(params)
        parts = msg['body'].strip().split(' ')
        prefix = extras['prefix']

        if len(parts) < 2 or parts[1].lower() != 'yes':
            mbody = f"This will delete all user state data. To confirm, type '{prefix}delete_user_states yes'."
            await self.bot.send_message_processed(mbody, msg=msg)
            return
        self.bot.sql.cursor.execute(f"DELETE FROM bot_user_states")
        self.bot.sql.conn.commit()
        self.bot._load_user_states()
        mbody = f"All user state data has been deleted."
        await self.bot.send_message_processed(mbody, msg=msg)
