import asyncio
import feedparser
import json
import hashlib
from .base_plugin import BasePlugin

# test feed
# https://lorem-rss.herokuapp.com/feed?unit=minute&interval=1

class Feeds(BasePlugin):
    def __init__(self, bot):
        super().__init__(bot)

    def register_variables(self):
        self.last_seen_articles = {}
        self.feeds = self.get_data("feeds.json", "json", fallback=[])
        self.frequency = self.config.get("frequency", 60*15)
        self.summarize_prompt = self.config.get("summarize_prompt") if self.bot.llm_enabled else None
        self.first_pass = True

    def apply_filter(self, entry, filter_config):
        if filter_config is None:
            return True
        if not isinstance(filter_config, list):
            self.log.error("Filter config is not a list")
            return False
        for condition in filter_config:
            if not isinstance(condition, dict):
                self.log.warning("Skipping invalid condition in filter config")
                continue
            if self._evaluate_condition(entry, condition):
                return True
        return False

    def _evaluate_condition(self, entry, condition):
        exclude = condition.get("exclude", False)
        self.log.debug(f"Evaluating condition: {condition}, exclude={exclude}")

        if "field" in condition and "value" in condition:
            field_value = self._get_nested_field(entry, condition["field"])
            result = field_value == condition["value"]
            self.log.debug(f"Checking field '{condition['field']}' ({field_value}) == '{condition['value']}': {result}")
            return not result if exclude else result

        if "field" in condition and "contains" in condition:
            field_value = self._get_nested_field(entry, condition["field"], default="")
            result = condition["contains"] in field_value
            self.log.debug(f"Checking field '{condition['field']}' ({field_value}) contains '{condition['contains']}': {result}")
            return not result if exclude else result

        if "field" in condition and "starts_with" in condition:
            field_value = self._get_nested_field(entry, condition["field"], default="")
            result = field_value.startswith(condition["starts_with"])
            self.log.debug(f"Checking field '{condition['field']}' ({field_value}) starts with '{condition['starts_with']}': {result}")
            return not result if exclude else result

        if "field" in condition and "ends_with" in condition:
            field_value = self._get_nested_field(entry, condition["field"], default="")
            result = field_value.endswith(condition["ends_with"])
            self.log.debug(f"Checking field '{condition['field']}' ({field_value}) ends with '{condition['ends_with']}': {result}")
            return not result if exclude else result

        if "field" in condition and "in_list" in condition:
            field_value = self._get_nested_field(entry, condition["field"])
            result = field_value in condition["in_list"]
            self.log.debug(f"Checking field '{condition['field']}' ({field_value}) in list {condition['in_list']}: {result}")
            return not result if exclude else result

        if "chance" in condition:
            entry_id = entry.get("id", "")
            if not entry_id:
                self.log.debug("Entry ID is missing, skipping 'chance' condition")
                return False
            hash_value = int(hashlib.sha256(entry_id.encode()).hexdigest(), 16)
            probability = (hash_value % 10000) / 10000
            chance = condition["chance"]
            result = probability <= chance
            self.log.debug(f"Checking 'chance' condition: {probability} <= {chance}: {result}")
            return result

        self.log.warning(f"Unsupported condition: {condition}")
        return False

    def _get_nested_field(self, data, field_path, default=None):
        keys = field_path.split(".")
        value = data
        self.log.debug(f"Initial value: {value}")

        for key in keys:
            if value is None:
                self.log.debug(f"Value is None at key: {key}, returning default")
                return default

            if "[" in key and key.endswith("]"):
                list_key, index = key.split("[")
                index = int(index[:-1])
                self.log.debug(f"Processing list index: {list_key}[{index}]")

                if isinstance(value, dict):
                    value = value.get(list_key, default)
                if isinstance(value, (list, tuple)):
                    if 0 <= index < len(value):
                        value = value[index]
                    else:
                        self.log.debug(f"Invalid index: {index}, returning default")
                        return default
                else:
                    self.log.debug(f"Not a list or dict: {type(value)}, returning default")
                    return default

            elif isinstance(value, dict):
                self.log.debug(f"Processing dict key: {key}")
                value = value.get(key, default)

            elif isinstance(value, (list, tuple)):
                self.log.debug(f"Processing list key: {key}")
                new_value = []
                for item in value:
                    if isinstance(item, dict) and key in item:
                        new_value.append(item.get(key, default))
                value = new_value if new_value else default

            else:
                self.log.debug(f"Processing object attribute: {key}")
                value = getattr(value, key, default)

            self.log.debug(f"Intermediate value: {value}")

        self.log.debug(f"Final value: {value}")
        return value

    def register_tasks(self):
        self.create_periodic_task(self.check_feeds, self.frequency, task_name="feed_checker")

    async def check_feeds(self):
        self.log.debug("Starting feed check")
        for feed_info in self.feeds:
            try:
                feed_url = feed_info['url']
                mucs = feed_info['mucs']
                filter_config = feed_info.get('filter', None)
                self.log.debug(f"Processing feed: {feed_url}")

                feed = feedparser.parse(feed_url)
                if feed.bozo:
                    self.log.warning(f"Feed parsing error for {feed_url}: {feed.bozo_exception}")
                    continue

                new_entries = []
                for entry in feed.entries:
                    if entry.get('id') != self.last_seen_articles.get(feed_url):
                        if self.apply_filter(entry, filter_config):
                            self.log.debug(f"New entry found in {feed_url}: {entry.get('title')}")
                            new_entries.append(entry)
                        else:
                            self.log.debug(f"Skipping entry in {feed_url} due to filter")
                    else:
                        self.log.debug(f"Reached previously seen entries in {feed_url}")
                        break

                for entry in reversed(new_entries):
                    self.last_seen_articles[feed_url] = entry.get('id')
                    if self.first_pass:
                        self.log.debug("Skipping entry on first pass")
                        continue

                    if entry.description and self.summarize_prompt:
                        self.log.debug(f"Summarizing entry from {feed_url}")
                        mbody = f"🌐 *{entry.title}*\n{
                            await self.bot.llm.send_prompt(
                                self.summarize_prompt,
                                {'article': entry.description},
                                {'url': f" {entry.link} "})
                            }"
                        mbody = mbody.replace('  ', ' ')
                    else:
                        mbody = f"{entry.link}\n🌐 *{entry.title}*"

                    for muc in mucs:
                        self.log.debug(f"Sending message to MUC: {muc}")
                        await self.bot.send_message_processed(mto=muc, mbody=mbody, mtype='groupchat')

            except Exception as e:
                self.log.error(f"Error processing feed {feed_url}: {str(e)}")

        self.first_pass = False
        self.log.debug("Completed feed check")
