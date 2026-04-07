"""
多平台音乐搜索引擎
优先使用 NeteaseCloudMusicApi (自部署)，QQ音乐/酷狗使用公开接口作为补充
"""
import logging
import aiohttp
import asyncio
from typing import List, Optional
from .models import Song

logger = logging.getLogger("astrbot_plugin_music_together")


class MusicAPI:
    """聚合音乐搜索API"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.timeout = aiohttp.ClientTimeout(total=15)
        self._session: Optional[aiohttp.ClientSession] = None

        # NeteaseCloudMusicApi 服务地址 (必须配置)
        self.netease_api = self.config.get("netease_api_url", "http://localhost:3000").rstrip("/")
        # 网易云登录 cookie (可选，登录后可获取VIP歌曲)
        self.netease_cookie = self.config.get("netease_cookie", "")

        logger.info(f"NeteaseCloudMusicApi 地址: {self.netease_api}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _netease_params(self) -> dict:
        """构建带cookie的请求参数"""
        params = {}
        if self.netease_cookie:
            params["cookie"] = self.netease_cookie
        return params

    # ================================================================
    #  NeteaseCloudMusicApi 接口 (主要音乐源)
    #  项目地址: https://github.com/Binaryify/NeteaseCloudMusicApi
    #  部署后默认运行在 http://localhost:3000
    # ================================================================

    async def search_netease(self, keyword: str, limit: int = 5) -> List[Song]:
        """网易云音乐搜索 (通过 NeteaseCloudMusicApi)"""
        songs = []
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/cloudsearch"
            params = {
                "keywords": keyword,
                "type": 1,  # 单曲
                "limit": limit,
                "offset": 0,
                **self._netease_params(),
            }
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 200:
                        for item in data.get("result", {}).get("songs", [])[:limit]:
                            artists = "/".join([a["name"] for a in item.get("ar", [])])
                            album_info = item.get("al", {})
                            song = Song(
                                title=item.get("name", ""),
                                artist=artists,
                                album=album_info.get("name", ""),
                                duration=item.get("dt", 0) // 1000,
                                song_id=str(item.get("id", "")),
                                source="netease",
                                cover_url=album_info.get("picUrl", ""),
                            )
                            songs.append(song)
        except aiohttp.ClientConnectorError:
            logger.error(f"无法连接 NeteaseCloudMusicApi ({self.netease_api})，请检查服务是否启动")
        except Exception as e:
            logger.warning(f"网易云搜索失败: {e}")
        return songs

    async def get_netease_url(self, song_id: str, quality: str = "standard") -> str:
        """获取网易云音乐播放链接 (通过 NeteaseCloudMusicApi)

        quality: standard / higher / exhigh / lossless / hires / jyeffect / sky / jymaster
        """
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/song/url/v1"
            params = {
                "id": song_id,
                "level": quality,
                **self._netease_params(),
            }
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 200:
                        song_data = data.get("data", [])
                        if song_data and song_data[0].get("url"):
                            return song_data[0]["url"]
            # 降级: 使用外链
            logger.debug(f"NeteaseCloudMusicApi 未返回链接，使用外链降级")
            return f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
        except aiohttp.ClientConnectorError:
            return f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
        except Exception as e:
            logger.warning(f"获取网易云链接失败: {e}")
            return f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"

    async def get_netease_lyric(self, song_id: str) -> str:
        """获取网易云歌词 (通过 NeteaseCloudMusicApi)"""
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/lyric"
            params = {"id": song_id, **self._netease_params()}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("lrc", {}).get("lyric", "")
        except aiohttp.ClientConnectorError:
            # 降级: 使用公开API
            try:
                session = await self._get_session()
                fallback_url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1"
                headers = {
                    "Referer": "https://music.163.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }
                async with session.get(fallback_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("lrc", {}).get("lyric", "")
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"获取网易云歌词失败: {e}")
        return ""

    async def get_netease_song_detail(self, song_id: str) -> Optional[Song]:
        """获取歌曲详情 (通过 NeteaseCloudMusicApi)"""
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/song/detail"
            params = {"ids": song_id, **self._netease_params()}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    songs = data.get("songs", [])
                    if songs:
                        item = songs[0]
                        artists = "/".join([a["name"] for a in item.get("ar", [])])
                        album_info = item.get("al", {})
                        return Song(
                            title=item.get("name", ""),
                            artist=artists,
                            album=album_info.get("name", ""),
                            duration=item.get("dt", 0) // 1000,
                            song_id=str(item.get("id", "")),
                            source="netease",
                            cover_url=album_info.get("picUrl", ""),
                        )
        except Exception as e:
            logger.warning(f"获取歌曲详情失败: {e}")
        return None

    async def get_netease_hot_comments(self, song_id: str, limit: int = 5) -> List[str]:
        """获取网易云热门评论 (通过 NeteaseCloudMusicApi)"""
        comments = []
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/comment/hot"
            params = {
                "id": song_id,
                "type": 0,  # 歌曲
                "limit": limit,
                **self._netease_params(),
            }
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("hotComments", [])[:limit]:
                        user = item.get("user", {}).get("nickname", "匿名")
                        content = item.get("content", "")
                        likes = item.get("likedCount", 0)
                        comments.append(f"{user}: {content} ({likes}赞)")
        except Exception as e:
            logger.warning(f"获取热门评论失败: {e}")
        return comments

    async def get_netease_recommend(self, limit: int = 10) -> List[Song]:
        """获取每日推荐 (需要登录cookie)"""
        songs = []
        if not self.netease_cookie:
            logger.debug("未配置cookie，无法获取每日推荐")
            return songs
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/recommend/songs"
            params = self._netease_params()
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 200:
                        for item in data.get("data", {}).get("dailySongs", [])[:limit]:
                            artists = "/".join([a["name"] for a in item.get("ar", [])])
                            album_info = item.get("al", {})
                            song = Song(
                                title=item.get("name", ""),
                                artist=artists,
                                album=album_info.get("name", ""),
                                duration=item.get("dt", 0) // 1000,
                                song_id=str(item.get("id", "")),
                                source="netease",
                                cover_url=album_info.get("picUrl", ""),
                            )
                            songs.append(song)
        except Exception as e:
            logger.warning(f"获取每日推荐失败: {e}")
        return songs

    # ================================================================
    #  QQ音乐 (公开接口，作为补充音乐源)
    # ================================================================

    async def search_qqmusic(self, keyword: str, limit: int = 5) -> List[Song]:
        """QQ音乐搜索"""
        songs = []
        try:
            session = await self._get_session()
            url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
            params = {
                "w": keyword,
                "format": "json",
                "p": 1,
                "n": limit,
                "cr": 1,
                "new_json": 1,
            }
            headers = {
                "Referer": "https://y.qq.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    song_list = data.get("data", {}).get("song", {}).get("list", [])
                    for item in song_list[:limit]:
                        artists = "/".join([s.get("name", "") for s in item.get("singer", [])])
                        mid = item.get("mid", "")
                        album_mid = item.get("album", {}).get("mid", "")
                        song = Song(
                            title=item.get("name", ""),
                            artist=artists,
                            album=item.get("album", {}).get("name", ""),
                            duration=item.get("interval", 0),
                            song_id=mid,
                            source="qqmusic",
                            cover_url=f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg" if album_mid else "",
                        )
                        songs.append(song)
        except Exception as e:
            logger.warning(f"QQ音乐搜索失败: {e}")
        return songs

    async def get_qqmusic_url(self, song_mid: str) -> str:
        """获取QQ音乐播放链接 (公开外链，部分歌曲可能不可用)"""
        try:
            return f"https://isure.stream.qqmusic.qq.com/C400{song_mid}.m4a?fromtag=38"
        except Exception as e:
            logger.warning(f"获取QQ音乐链接失败: {e}")
            return ""

    # ================================================================
    #  酷狗音乐 (公开接口，作为补充音乐源)
    # ================================================================

    async def search_kugou(self, keyword: str, limit: int = 5) -> List[Song]:
        """酷狗音乐搜索"""
        songs = []
        try:
            session = await self._get_session()
            url = "https://mobilecdn.kugou.com/api/v3/search/song"
            params = {
                "format": "json",
                "keyword": keyword,
                "page": 1,
                "pagesize": limit,
                "showtype": 1,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("status") == 1:
                        for item in data.get("data", {}).get("info", [])[:limit]:
                            song = Song(
                                title=item.get("songname", ""),
                                artist=item.get("singername", ""),
                                album=item.get("album_name", ""),
                                duration=item.get("duration", 0),
                                song_id=item.get("hash", ""),
                                source="kugou",
                            )
                            songs.append(song)
        except Exception as e:
            logger.warning(f"酷狗搜索失败: {e}")
        return songs

    async def get_kugou_url(self, hash_id: str) -> str:
        """获取酷狗播放链接"""
        try:
            session = await self._get_session()
            url = "https://wwwapi.kugou.com/yy/index.php"
            params = {"r": "play/getdata", "hash": hash_id}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie": "kg_mid=1",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return data.get("data", {}).get("play_url", "")
        except Exception as e:
            logger.warning(f"获取酷狗链接失败: {e}")
        return ""

    # ================================================================
    #  聚合接口
    # ================================================================

    async def search(self, keyword: str, source: str = "all", limit: int = 5) -> List[Song]:
        """聚合搜索"""
        if source == "netease":
            return await self.search_netease(keyword, limit)
        elif source == "qqmusic":
            return await self.search_qqmusic(keyword, limit)
        elif source == "kugou":
            return await self.search_kugou(keyword, limit)
        else:
            # 并发搜索所有平台，网易云多分配一些
            tasks = [
                self.search_netease(keyword, limit=4),
                self.search_qqmusic(keyword, limit=3),
                self.search_kugou(keyword, limit=3),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            songs = []
            for result in results:
                if isinstance(result, list):
                    songs.extend(result)
            return songs[:limit]

    async def get_play_url(self, song: Song) -> str:
        """获取歌曲播放链接"""
        if song.url:
            return song.url
        if song.source == "netease":
            quality = self.config.get("music_quality", "standard")
            return await self.get_netease_url(song.song_id, quality)
        elif song.source == "qqmusic":
            return await self.get_qqmusic_url(song.song_id)
        elif song.source == "kugou":
            return await self.get_kugou_url(song.song_id)
        return ""

    async def get_lyric(self, song: Song) -> str:
        """获取歌词"""
        if song.lyric:
            return song.lyric
        if song.source == "netease":
            return await self.get_netease_lyric(song.song_id)
        return ""

    async def get_hot_songs(self, source: str = "netease") -> List[Song]:
        """获取热门排行榜 (通过 NeteaseCloudMusicApi)"""
        try:
            session = await self._get_session()
            # 使用 NeteaseCloudMusicApi 的排行榜接口
            # 飙升榜=19723756, 新歌榜=3779629, 原创榜=2884035, 热歌榜=3778678
            url = f"{self.netease_api}/playlist/track/all"
            params = {
                "id": "3778678",  # 热歌榜
                "limit": 20,
                "offset": 0,
                **self._netease_params(),
            }
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    songs = []
                    for item in data.get("songs", [])[:20]:
                        artists = "/".join([a["name"] for a in item.get("ar", [])])
                        album_info = item.get("al", {})
                        song = Song(
                            title=item.get("name", ""),
                            artist=artists,
                            album=album_info.get("name", ""),
                            duration=item.get("dt", 0) // 1000,
                            song_id=str(item.get("id", "")),
                            source="netease",
                            cover_url=album_info.get("picUrl", ""),
                        )
                        songs.append(song)
                    return songs
        except aiohttp.ClientConnectorError:
            logger.error(f"无法连接 NeteaseCloudMusicApi，排行榜获取失败")
        except Exception as e:
            logger.warning(f"获取热门排行榜失败: {e}")

        # 降级: 使用公开API
        return await self._get_hot_songs_fallback()

    async def _get_hot_songs_fallback(self) -> List[Song]:
        """排行榜降级方案"""
        try:
            session = await self._get_session()
            url = "https://music.163.com/api/playlist/detail"
            params = {"id": "3778678"}
            headers = {
                "Referer": "https://music.163.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    songs = []
                    for item in data.get("result", {}).get("tracks", [])[:20]:
                        artists = "/".join([a["name"] for a in item.get("artists", [])])
                        song = Song(
                            title=item.get("name", ""),
                            artist=artists,
                            album=item.get("album", {}).get("name", ""),
                            duration=item.get("duration", 0) // 1000,
                            song_id=str(item.get("id", "")),
                            source="netease",
                        )
                        songs.append(song)
                    return songs
        except Exception as e:
            logger.warning(f"排行榜降级方案也失败: {e}")
        return []

    async def check_api_status(self) -> bool:
        """检查 NeteaseCloudMusicApi 服务是否可用"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.netease_api}/") as resp:
                return resp.status == 200
        except Exception:
            return False
