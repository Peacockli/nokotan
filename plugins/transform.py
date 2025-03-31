import os
import asyncio
from .base_plugin import BasePlugin

prompts_folder = "data/prompts"

class Transform(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)

    def register_commands(self):
        if not self.bot.llm_enabled:
            self.log.warning(f"llm not enabled, not registering any transform commands.")
            return
        for prompt_folder in os.listdir(prompts_folder):
            prompt_folder_path = os.path.join(prompts_folder, prompt_folder)
            if os.path.isdir(prompt_folder_path) and "_filter" in str(prompt_folder):
                command_name = prompt_folder.replace("_filter", "")
                self.bot.register_command(command_name, self.plugin_name, self.transform_command, category="transform", cooldown=30)

    async def transform_command(self, params):
        """Transform text into a given style
        Usage: <prefix><style> <text>, [reply]<prefix><style>
        Use <prefix>h transform to view available styles.
        """
        extras = await self.get_extras_cmd(params)
        msg = params['msg']
        parts = msg['body'].split(' ')
        quote = params['quote']
        transform = parts[0].replace(extras['prefix'], "")
        transform_prompt = f"{transform}_filter"
        if quote:
            text = quote
        elif len(parts) > 1:
            text = ' '.join(parts[1:])
        else:
            text = None

        if text:
            mbody = await self.bot.llm.send_prompt(transform_prompt, {"text": text})
        else:
            mbody = f"Usage: {extras['prefix']}<style> <text>, [reply]{extras['prefix']}<style>"
        await self.bot.send_message_processed(mbody, msg=msg)
