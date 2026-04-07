"""数据持久化模块"""
import json
import os
import logging
from typing import Optional
from .models import SharedPlaylist, UserData

logger = logging.getLogger("astrbot_plugin_music_together")


class Storage:
    """JSON文件持久化存储"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.playlists_dir = os.path.join(data_dir, "playlists")
        self.users_dir = os.path.join(data_dir, "users")
        os.makedirs(self.playlists_dir, exist_ok=True)
        os.makedirs(self.users_dir, exist_ok=True)

    def _safe_filename(self, name: str) -> str:
        """将session_id/user_id转为安全文件名"""
        return name.replace("/", "_").replace("\\", "_").replace(":", "_")

    # ==================== 歌单存储 ====================

    def save_playlist(self, playlist: SharedPlaylist):
        """保存共享歌单"""
        filename = self._safe_filename(playlist.session_id) + ".json"
        filepath = os.path.join(self.playlists_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(playlist.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存歌单失败: {e}")

    def load_playlist(self, session_id: str) -> Optional[SharedPlaylist]:
        """加载共享歌单"""
        filename = self._safe_filename(session_id) + ".json"
        filepath = os.path.join(self.playlists_dir, filename)
        try:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return SharedPlaylist.from_dict(data)
        except Exception as e:
            logger.error(f"加载歌单失败: {e}")
        return None

    # ==================== 用户数据存储 ====================

    def save_user(self, user_data: UserData):
        """保存用户数据"""
        filename = self._safe_filename(user_data.user_id) + ".json"
        filepath = os.path.join(self.users_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(user_data.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存用户数据失败: {e}")

    def load_user(self, user_id: str) -> UserData:
        """加载用户数据，不存在则创建新的"""
        filename = self._safe_filename(user_id) + ".json"
        filepath = os.path.join(self.users_dir, filename)
        try:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return UserData.from_dict(data)
        except Exception as e:
            logger.error(f"加载用户数据失败: {e}")
        return UserData(user_id=user_id)
