"""数据模型定义"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Song:
    """歌曲信息"""
    title: str
    artist: str
    album: str = ""
    duration: int = 0  # 秒
    song_id: str = ""
    source: str = ""  # netease / qqmusic / kugou
    url: str = ""  # 播放链接
    cover_url: str = ""  # 封面图
    lyric: str = ""  # 歌词文本

    def display(self, index: int = 0) -> str:
        prefix = f"{index}. " if index > 0 else ""
        duration_str = ""
        if self.duration > 0:
            m, s = divmod(self.duration, 60)
            duration_str = f" [{m}:{s:02d}]"
        src_map = {"netease": "网易云", "qqmusic": "QQ音乐", "kugou": "酷狗"}
        src = src_map.get(self.source, self.source)
        return f"{prefix}{self.title} - {self.artist}{duration_str} ({src})"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
            "song_id": self.song_id,
            "source": self.source,
            "url": self.url,
            "cover_url": self.cover_url,
            "lyric": self.lyric,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Song":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PlaylistEntry:
    """歌单条目"""
    song: Song
    added_by: str = ""  # 添加者ID
    added_by_name: str = ""  # 添加者昵称
    added_at: float = 0.0  # 添加时间戳
    votes: list = field(default_factory=list)  # 投票用户ID列表

    @property
    def vote_count(self) -> int:
        return len(self.votes)

    def to_dict(self) -> dict:
        return {
            "song": self.song.to_dict(),
            "added_by": self.added_by,
            "added_by_name": self.added_by_name,
            "added_at": self.added_at,
            "votes": self.votes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlaylistEntry":
        song = Song.from_dict(data["song"])
        return cls(
            song=song,
            added_by=data.get("added_by", ""),
            added_by_name=data.get("added_by_name", ""),
            added_at=data.get("added_at", 0.0),
            votes=data.get("votes", []),
        )


@dataclass
class SharedPlaylist:
    """共享歌单 (每个群/会话一个)"""
    session_id: str
    entries: list = field(default_factory=list)  # List[PlaylistEntry]
    current_index: int = 0
    skip_votes: list = field(default_factory=list)  # 切歌投票
    created_at: float = field(default_factory=time.time)

    @property
    def current_song(self) -> Optional[PlaylistEntry]:
        if 0 <= self.current_index < len(self.entries):
            return self.entries[self.current_index]
        return None

    def add_song(self, song: Song, user_id: str, user_name: str) -> int:
        entry = PlaylistEntry(
            song=song,
            added_by=user_id,
            added_by_name=user_name,
            added_at=time.time(),
        )
        self.entries.append(entry)
        return len(self.entries)

    def next_song(self) -> Optional[PlaylistEntry]:
        self.skip_votes.clear()
        if self.current_index < len(self.entries) - 1:
            self.current_index += 1
            return self.current_song
        return None

    def vote_skip(self, user_id: str) -> int:
        if user_id not in self.skip_votes:
            self.skip_votes.append(user_id)
        return len(self.skip_votes)

    def vote_song(self, index: int, user_id: str) -> bool:
        if 0 <= index < len(self.entries):
            entry = self.entries[index]
            if user_id not in entry.votes:
                entry.votes.append(user_id)
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "entries": [e.to_dict() for e in self.entries],
            "current_index": self.current_index,
            "skip_votes": self.skip_votes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SharedPlaylist":
        playlist = cls(
            session_id=data["session_id"],
            current_index=data.get("current_index", 0),
            skip_votes=data.get("skip_votes", []),
            created_at=data.get("created_at", time.time()),
        )
        playlist.entries = [PlaylistEntry.from_dict(e) for e in data.get("entries", [])]
        return playlist


@dataclass
class UserData:
    """用户数据"""
    user_id: str
    favorites: list = field(default_factory=list)  # List[dict] (Song.to_dict)
    play_history: list = field(default_factory=list)  # List[dict]
    play_count: dict = field(default_factory=dict)  # {source_songid: count}

    def add_favorite(self, song: Song) -> bool:
        key = f"{song.source}_{song.song_id}"
        for fav in self.favorites:
            if f"{fav['source']}_{fav['song_id']}" == key:
                return False
        self.favorites.append(song.to_dict())
        return True

    def remove_favorite(self, index: int) -> bool:
        if 0 <= index < len(self.favorites):
            self.favorites.pop(index)
            return True
        return False

    def add_to_history(self, song: Song):
        key = f"{song.source}_{song.song_id}"
        self.play_count[key] = self.play_count.get(key, 0) + 1
        # 保留最近100条
        self.play_history.append({
            **song.to_dict(),
            "played_at": time.time(),
        })
        if len(self.play_history) > 100:
            self.play_history = self.play_history[-100:]

    def get_top_songs(self, limit: int = 10) -> list:
        """获取最常听的歌曲"""
        sorted_songs = sorted(self.play_count.items(), key=lambda x: x[1], reverse=True)
        result = []
        for key, count in sorted_songs[:limit]:
            for h in reversed(self.play_history):
                if f"{h['source']}_{h['song_id']}" == key:
                    result.append({"song": h, "count": count})
                    break
        return result

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "favorites": self.favorites,
            "play_history": self.play_history,
            "play_count": self.play_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserData":
        return cls(
            user_id=data["user_id"],
            favorites=data.get("favorites", []),
            play_history=data.get("play_history", []),
            play_count=data.get("play_count", {}),
        )
