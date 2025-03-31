import os
import json

from langchain_ollama.chat_models import ChatOllama
from langchain_openai.chat_models import ChatOpenAI
from typing import List, Dict, Optional, Union
from contextlib import contextmanager

# TODO:
#     use logger instead of print
#     more organized prompt directory with categories
#     config wether to use ollama or openai as primary/fallback or have no fallback
#     config prompts prompts_directory

prompts_directory = "data/prompts"

class BaseChatInterface:
    def __init__(self):
        self.prompts = self._load_prompts(prompts_directory)

    def reload_prompts(self):
        self.prompts = self._load_prompts(prompts_directory)

    def _load_prompts(self, base_dir: str) -> Dict[str, List[Dict]]:
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)

        prompts_dict = {}
        for prompt_folder in os.listdir(base_dir):
            prompt_folder_path = os.path.join(base_dir, prompt_folder)
            if os.path.isdir(prompt_folder_path):
                prompt_config = self._load_prompt_config(prompt_folder_path)
                if prompt_config:
                    prompt_structure = self._build_prompt_structure(prompt_config, prompt_folder_path)
                    prompts_dict[prompt_folder] = prompt_structure
        return prompts_dict

    def _load_prompt_config(self, prompt_folder_path: str) -> Optional[List[Dict]]:
        prompt_json_path = os.path.join(prompt_folder_path, 'prompt.json')
        if os.path.exists(prompt_json_path):
            with open(prompt_json_path, 'r') as f:
                return json.load(f)
        return None

    def _build_prompt_structure(self, prompt_config: Dict, prompt_folder_path: str) -> Dict:
        prompt_messages = []
        output_replacements = None
        for item in prompt_config.get("messages", []):
            role = item['role']
            file = item['file']
            file_path = os.path.join(prompt_folder_path, file)
            content = self._load_file_content(file_path)
            if "input_replacements" in item:
                prompt_messages.append({role: content, "input_replacements": item["input_replacements"]})
            else:
                prompt_messages.append({role: content})

        if "output_replacements" in prompt_config:
            output_replacements = prompt_config["output_replacements"]
        return {"messages": prompt_messages, "output_replacements": output_replacements}

    def _load_file_content(self, file_path: str) -> str:
        with open(file_path, 'r') as f:
            return f.read()

    async def send_message(self, system_prompt: Optional[str] = None, user_message: str = "") -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        response = await self.llm.ainvoke(messages)
        return response.content

    async def send_multiple_messages(self, messages: List[Dict[str, str]]) -> str:
        formatted_messages = []
        for message in messages:
            for role, content in message.items():
                formatted_messages.append({"role": role, "content": content})
        response = await self.llm.ainvoke(formatted_messages)
        return response.content

    async def send_prompt(self, prompt: str, input_replacements: Optional[Dict[str, str]] = None, output_replacements: Optional[Dict[str, str]] = None) -> str:
        prompt_data = self.prompts.get(prompt)
        if not prompt_data:
            raise KeyError(f"Prompt '{prompt}' not found.")
        if input_replacements:
            input_replacements = {f"{{{key}}}": value for key, value in input_replacements.items()}
        if output_replacements:
            output_replacements = {f"{{{key}}}": value for key, value in output_replacements.items()}
        prompt_messages = prompt_data["messages"]
        prompt_output_replacements = prompt_data.get("output_replacements")
        processed_messages = []
        for message in prompt_messages:
            role = list(message.keys())[0]
            content = message[role]
            if "input_replacements" in message:
                if not input_replacements:
                    raise ValueError(f"Prompt '{prompt}' expects input replacements, but none were provided.")

                for placeholder in message["input_replacements"].values():
                    if placeholder not in input_replacements:
                        raise ValueError(f"Expected input '{placeholder}' is missing in the provided input replacements.")

                for placeholder, value in message["input_replacements"].items():
                    content = content.replace(f"{value}", input_replacements.get(value, ""))
            processed_messages.append({role: content})

        response = await self.send_multiple_messages(processed_messages)

        if prompt_output_replacements and output_replacements:
            for placeholder, value in prompt_output_replacements.items():
                response = response.replace(f"{value}", output_replacements.get(value, ""))

        return response

class OllamaChatInterface(BaseChatInterface):
    def __init__(self, host: str, model: str, temperature: float = 1.2, num_predict: int = 256):
        super().__init__()
        self.default_temperature = temperature
        self.default_num_predict = num_predict
        self.default_model = model
        self.llm = ChatOllama(
            base_url=host,
            model=model,
            temperature=temperature,
            num_predict=num_predict,
        )

class OpenAIChatInterface(BaseChatInterface):
    def __init__(self, api_key: str, host: str, model: str, temperature: float, num_predict: int):
        super().__init__()
        self.default_temperature = temperature
        self.default_max_tokens = num_predict
        self.default_model = model
        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url=host,
            model=model,
            temperature=temperature,
            max_tokens=num_predict,
        )

class ChatOrchestrator:
    def __init__(self, openai_api_key: str, openai_host: str, openai_model: str,
                 ollama_host: str, ollama_model: str,
                 llm_temperature: float = 1.2, llm_num_predict: int = 256):
        self.openai_interface = OpenAIChatInterface(openai_api_key, openai_host, openai_model, llm_temperature, llm_num_predict)
        self.ollama_interface = OllamaChatInterface(ollama_host, ollama_model, llm_temperature, llm_num_predict)

    @contextmanager
    def _set_llm_parameters(self, temperature: Optional[float] = None, num_predict: Optional[int] = None, model: Optional[str] = None):
        try:
            if temperature:
                self.openai_interface.llm.temperature = temperature
                self.ollama_interface.llm.temperature = temperature
            if num_predict:
                self.openai_interface.llm.max_tokens = num_predict
                self.ollama_interface.llm.num_predict = num_predict
            if model:
                self.openai_interface.llm.model_name = model
                self.ollama_interface.llm.model = model
            yield
        finally:
            if temperature:
                self.openai_interface.llm.temperature = self.openai_interface.default_temperature
                self.ollama_interface.llm.temperature = self.ollama_interface.default_temperature
            if num_predict:
                self.openai_interface.llm.max_tokens = self.openai_interface.default_max_tokens
                self.ollama_interface.llm.num_predict = self.ollama_interface.default_num_predict
            if model:
                self.openai_interface.llm.model_name = self.openai_interface.default_model
                self.ollama_interface.llm.model = self.ollama_interface.default_model

    async def send_message(self, system_prompt: Optional[str] = None, user_message: str = "",
                           temperature: Optional[float] = None, num_predict: Optional[int] = None, model: Optional[str] = None) -> str:
        with self._set_llm_parameters(temperature, num_predict, model):
            try:
                return await self.openai_interface.send_message(system_prompt, user_message)
            except Exception as e:
                print(f"OpenAI failed, falling back to Ollama: {e}")
                return await self.ollama_interface.send_message(system_prompt, user_message)

    async def send_multiple_messages(self, messages: List[Dict[str, str]],
                                     temperature: Optional[float] = None, num_predict: Optional[int] = None, model: Optional[str] = None) -> str:
        with self._set_llm_parameters(temperature, num_predict, model):
            try:
                return await self.openai_interface.send_multiple_messages(messages)
            except Exception as e:
                print(f"OpenAI failed, falling back to Ollama: {e}")
                return await self.ollama_interface.send_multiple_messages(messages)

    async def send_prompt(self, prompt: str, input_replacements: Optional[Dict[str, str]] = None, output_replacements: Optional[Dict[str, str]] = None,
                          temperature: Optional[float] = None, num_predict: Optional[int] = None, model: Optional[str] = None) -> str:
        with self._set_llm_parameters(temperature, num_predict, model):
            try:
                return await self.openai_interface.send_prompt(prompt, input_replacements, output_replacements)
            except Exception as e:
                print(f"OpenAI failed, falling back to Ollama: {e}")
                return await self.ollama_interface.send_prompt(prompt, input_replacements, output_replacements)
