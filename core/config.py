import json
import os
import logging
import traceback

logger = logging.getLogger(__name__)

def load_config():
    try:
        with open("config", "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading config file: {e}\n{traceback.format_exc()}")
        config = {}

    env_var_map = {
        "NOKOTAN_JID": ("bot_jid", False),
        "NOKOTAN_PW": ("bot_pw", False),
        "NOKOTAN_NICK": ("bot_nick", False),
        "NOKOTAN_SQL_FILE": ("sql_file", False),
        "NOKOTAN_ADMINS": ("admins", True),
        "NOKOTAN_IGNORED": ("ignored", True),
        "NOKOTAN_CMD_COOLDOWN": ("default_command_cooldown", False),
        "NOKOTAN_CMD_PREFIX": ("default_command_prefix", False),
        "NOKOTAN_SHOW_SUGGEST": ("show_command_suggestion", True),
        "NOKOTAN_RUN_SUGGEST": ("run_command_suggestion", True),
        "NOKOTAN_MAX_HISTORY": ("max_history", False),
        "NOKOTAN_DISABED_PLUGINS": ("global_disabled_plugins", True),
        "NOKOTAN_LOG_ENABLED": ("logging_enabled", False),
        "NOKOTAN_LOG_LEVEL": ("logging_level", False),
        "NOKOTAN_LOG_TO_FILE": ("logging_log_to_file", False),
        "NOKOTAN_LOG_FILE_PATH": ("logging_log_file_path", False),
    }

    for env_var, (config_key, should_split) in env_var_map.items():
        if env_var in os.environ:
            value = os.environ[env_var]
            config[config_key] = value.split(',') if should_split else value

    logger.info("Configuration loaded successfully.")
    return config
