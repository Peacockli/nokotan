import sqlite3
from typing import Any, Optional, Dict, List

class SQLHandler:
    def __init__(self, sql_file: str):
        self.conn = sqlite3.connect(sql_file)
        self.cursor = self.conn.cursor()

    def _create_plugin_table(self, plugin_name: str):
        self.cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {plugin_name} (
                key TEXT NOT NULL,
                field TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (key, field)
            )
        """)
        self.conn.commit()

    def set(self, plugin_name: str, key: str, field: str, value: Any):
        self._create_plugin_table(plugin_name)
        self.cursor.execute(f"""
            INSERT OR REPLACE INTO {plugin_name} (key, field, value)
            VALUES (?, ?, ?)
        """, (key, field, str(value)))
        self.conn.commit()

    def get(self, plugin_name: str, key: str, field: str, fallback: Any=None) -> Optional[Any]:
        self._create_plugin_table(plugin_name)
        self.cursor.execute(f"""
            SELECT value FROM {plugin_name}
            WHERE key = ? AND field = ?
        """, (key, field))
        result = self.cursor.fetchone()
        return result[0] if result else fallback

    def get_all_fields(self, plugin_name: str, key: str) -> Dict[str, Any]:
        self._create_plugin_table(plugin_name)
        self.cursor.execute(f"""
            SELECT field, value FROM {plugin_name}
            WHERE key = ?
        """, (key,))
        return dict(self.cursor.fetchall())

    def get_all_keys(self, plugin_name: str) -> Dict[str, Dict[str, Any]]:
        self._create_plugin_table(plugin_name)
        self.cursor.execute(f"""
            SELECT key, field, value FROM {plugin_name}
        """)
        result = {}
        for key, field, value in self.cursor.fetchall():
            if key not in result:
                result[key] = {}
            result[key][field] = value
        return result

    def get_ordered_by(self, plugin_name: str, order_by_field: str, limit: int = None, descending: bool = True, key_pattern: str = None, offset: int = None, filter_field: str = None, filter_value: str = None) -> List[Dict[str, Any]]:
        self._create_plugin_table(plugin_name)
        order_direction = "DESC" if descending else "ASC"
        query = f"""
            SELECT key, value
            FROM {plugin_name}
            WHERE field = ?
            {"AND key LIKE ?" if key_pattern else ""}
            {f"AND key IN (SELECT key FROM {plugin_name} WHERE field = ? AND value = ?)" if filter_field else ""}
            ORDER BY CAST(value AS REAL) {order_direction}
            {"LIMIT ?" if limit else ""}
            {" OFFSET ?" if offset is not None else ""}
        """
        params = [order_by_field]
        if key_pattern:
            params.append(key_pattern)
        if filter_field:
            params.extend([filter_field, filter_value])
        if limit:
            params.append(limit)
        if offset is not None:
            params.append(offset)

        self.cursor.execute(query, params)
        result = self.cursor.fetchall()
        ordered_messages = []
        for key, _ in result:
            message = self.get_all_fields(plugin_name, key)
            ordered_messages.append(message)
        return ordered_messages

    def delete(self, plugin_name: str, key: str, field: str = None):
        self._create_plugin_table(plugin_name)
        if field:
            self.cursor.execute(f"""
                DELETE FROM {plugin_name}
                WHERE key = ? AND field = ?
            """, (key, field))
        else:
            self.cursor.execute(f"""
                DELETE FROM {plugin_name}
                WHERE key = ?
            """, (key,))
        self.conn.commit()

    def delete_all_fields(self, plugin_name: str, key: Optional[str] = None, key_pattern: Optional[str] = None):
        self._create_plugin_table(plugin_name)
        if key:
            self.cursor.execute(f"""
                DELETE FROM {plugin_name}
                WHERE key = ?
            """, (key,))
        elif key_pattern:
            self.cursor.execute(f"""
                DELETE FROM {plugin_name}
                WHERE key LIKE ?
            """, (key_pattern,))
        else:
            raise ValueError("Either 'key' or 'key_pattern' must be provided.")
        self.conn.commit()

    def delete_all_keys(self, plugin_name: str):
        self._create_plugin_table(plugin_name)
        self.cursor.execute(f"""
            DELETE FROM {plugin_name}
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()
