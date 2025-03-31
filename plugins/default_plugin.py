import os
import re
import traceback
import asyncio
from .base_plugin import BasePlugin

class DefaultPlugin(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)
        self.whisper_help = self.config.get("whisper_help", False)

    def register_commands(self):
        """Register commands with the bot"""
        self.bot.register_command('help', self.plugin_name, self.help_command, aliases=['h'])
        self.bot.register_command('join', self.plugin_name, self.join_command, admin=True, category='admin')
        self.bot.register_command('ignore', self.plugin_name, self.ignore_command, admin=True, category='admin')
        self.bot.register_command('shutdown', self.plugin_name, self.shutdown_command, admin=True, category='admin')
        self.bot.register_command('restart', self.plugin_name, self.restart_command, admin=True, category='admin', aliases=['reboot', 'reload'])
        self.bot.register_command('logs', self.plugin_name, self.logs_command, admin=True, category='admin')

    async def help_command(self, params):
        """
        List available commands and their usage.
        Usage: <prefix>help, <prefix>help <command|category.>
        """
        msg = params['msg']
        muc = params['muc']
        nick = params['nick']
        extras = await self.get_extras_cmd(params)
        jid = extras['jid']
        prefix = extras['prefix']
        categories = self.bot.command_handler.categories
        user_is_admin = jid in self.bot.admins
        commands = self.bot.command_handler.commands
        parts = msg['body'].split()
        if len(parts) == 1:
            help_text = ["Available commands:"]
            for name, command in commands.items():
                if command.hidden or (command.admin and not user_is_admin) or command.category:
                    continue
                help_text.append(f"{name},")
            help_text = " ".join(help_text)

            if categories:
                help_text += f"\n\n'{prefix}help <category>' to view categories.\nAvailable categories: "
                for category in categories.keys():
                    if category == 'admin' and not user_is_admin: continue
                    help_text += f"{category}, "
        if len(parts) == 2:
            command_or_category = parts[1]
            command = self.bot.command_handler.commands.get(command_or_category)
            if command and (not command.admin or user_is_admin):
                help_text = command.function.__doc__ or f"No documentation found for {command_or_category}"
            elif command_or_category in categories:
                help_text = f"Commands in category '{command_or_category}':"
                for cmd in categories[command_or_category]:
                    if not cmd.hidden and (not cmd.admin or user_is_admin):
                        help_text += f" {cmd.name},"
            else:
                help_text = f"No command or category named '{command_or_category}' found."

        if len(parts) >= 3:
            help_text = self.help_command.__doc__

        if self.whisper_help or msg['type'] == 'chat':
            mto = msg['from']
            mtype = 'chat'
        else:
            mto = msg['from'].bare
            mtype = 'groupchat'
        mbody = self.remove_leading_whitespace(help_text).replace('<prefix>', prefix)

        await self.bot.send_message_processed(mbody, msg=msg)

    def remove_leading_whitespace(self, text):
        lines = text.splitlines()
        stripped_lines = [re.sub(r"^\s+", "", line) for line in lines]
        return "\n".join(stripped_lines)

    async def join_command(self, params):
        """Have the bot join a muc.
        Usage: '<prefix>join <muc_jid>'"""
        msg = params['msg']
        muc = params['muc']
        command = msg['body'].strip()
        parts = command.split(' ')
        mbody = self.join_command.__doc__
        if len(parts)== 2 and self.is_valid_jid(parts[1]):
            muc_jid = parts[1]
            await self.bot.join_muc(muc_jid, self.bot.nick)
            mbody = f"Joining {muc_jid}"
        await self.bot.send_message_processed(mbody, msg=msg)

    async def ignore_command(self, params):
        """Add or remove nicks for the bot to ignore.
        Usage: '<prefix>ignore add|remove <nick1> [nick2...], <prefix>ignore list'"""
        jid = params['jid']
        msg = params['msg']
        command = msg['body'].strip()
        parts = command.split(' ')
        message = self.ignore_command.__doc__
        if len(parts) == 2 and parts[1] == "list":
            message = f"Ignored users: {', '.join(self.bot.ignored)}"
        if len(parts) == 3 and parts[1] == "who":
            admin = self.bot.get_ignore_admin(parts[2])
            message = f"{parts[2]} was added to ignore by: {admin}"
        if len(parts) > 2:
            for nick in parts[2:]:
                if parts[1] == "add":
                    if nick not in self.bot.ignored:
                        self.bot.add_ignore(nick, jid)
                elif parts[1] == "remove":
                    if nick in self.bot.ignored:
                        self.bot.remove_ignore(nick)
            if parts[1] == "add":
                message = f"Ignoring {', '.join(parts[2:])}"
            elif parts[1] == "remove":
                message = f"No longer ignoring {', '.join(parts[2:])}"
        await self.bot.send_message_processed(message, msg=msg)

    async def logs_command(self, params):
        """Returns the last x lines of logs (logfile required)
        Usage: <prefix>logs|<prefix>logs <num>|<prefix>logs <start> <num>"""
        msg = params['msg']
        if msg['type'] == 'groupchat':
            return
        log_to_file = self.bot.config.get('logging', {}).get('log_to_file')
        log_file_path = self.bot.config.get('logging', {}).get('log_file_path')
        if not log_to_file:
            mbody = "Logging to file is disabled or not set in config."
            await self.bot.send_message_processed(mbody, msg=msg)
            return
        elif not log_file_path:
            mbody = "Log file path is not set in config."
            await self.bot.send_message_processed(mbody, msg=msg)
            return

        parts = msg['body'].strip().split(' ')
        if len(parts) == 1:
            start = 1
            num = 10
        elif len(parts) == 2:
            start = 1
            num = int(parts[1])
        elif len(parts) == 3:
            start = int(parts[1])
            num = int(parts[2])
        else:
            extras = self.get_extras_cmd(params)
            prefix = extras['prefix']
            mbody = f"Usage: {prefix}logs|{prefix}logs <num>|{prefix}logs <start> <num>"
            await self.bot.send_message_processed(mbody, msg=msg)
            return

        with open(log_file_path, 'r', encoding='latin-1') as f: # latinmeme 2 prevent decode error on emojis
            f.seek(0, os.SEEK_END)
            buffer = ''
            lines_found = 0
            skipped_lines = 0
            position = f.tell()

            while position >= 0 and skipped_lines < start:
                f.seek(position)
                char = f.read(1)
                if char == '\n':
                    skipped_lines += 1
                position -= 1

            while position >= 0 and lines_found <= num:
                f.seek(position)
                char = f.read(1)
                if char == '\n':
                    lines_found += 1
                buffer = char + buffer
                position -= 1

        lines = buffer.splitlines()
        mbody = '\n'.join(lines[-num:])
        await self.bot.send_message_processed(mbody, msg=msg)

    async def shutdown_command(self, params):
        """Shuts down the bot process.
        Usage: <prefix>shutdown"""
        await self.bot.send_message_processed("Going to sleep...", msg=params['msg'])
        await self.bot.shutdown()

    async def restart_command(self, params):
        """Restarts the bot process, loading any new changes.
        Usage: <prefix>restart"""
        await self.bot.send_message_processed("Taking a quick nap...", msg=params['msg'])
        await self.bot.restart()

    def is_valid_jid(self, jid):
        jid_pattern = r'^(?:[^"&\'/:<>@]{1,1023}@)?[^"&\'/:<>@]+\.[^"&\'/:<>@]+$'
        return bool(re.match(jid_pattern, jid))
