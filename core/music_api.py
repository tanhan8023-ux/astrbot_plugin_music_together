"""
多平台音乐搜索引擎
优先使用 NeteaseCloudMusicApi (自部署)，QQ音乐/酷狗使用公开接口作为补充
"""
import re
import logging
import aiohttp
import asyncio
from typing import List, Optional, Tuple
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

    # ================================================================
    #  网易云用户最近播放记录
    # ================================================================

    async def get_recent_songs(self, limit: int = 10, cookie: str = "") -> List[dict]:
        """获取网易云用户最近播放的歌曲

        Args:
            limit: 返回数量
            cookie: 用户的网易云cookie，为空则使用全局配置的cookie

        返回 List[dict]，每项包含 song (Song对象) 和 play_time (播放时间戳ms)
        """
        use_cookie = cookie or self.netease_cookie
        if not use_cookie:
            logger.debug("未配置cookie，无法获取最近播放记录")
            return []
        results = []
        try:
            session = await self._get_session()
            url = f"{self.netease_api}/record/recent/song"
            # cookie 同时通过 query param 和 header 传递，兼容不同版本的 NeteaseCloudMusicApi
            params = {"limit": limit, "cookie": use_cookie}
            headers = {}
            # 如果 cookie 看起来像标准 cookie 格式 (含 MUSIC_U)，也放到 header 里
            if "MUSIC_U" in use_cookie or "=" in use_cookie:
                cookie_header = use_cookie
                # 如果用户只传了 MUSIC_U=xxx，补全格式
                if not cookie_header.startswith("MUSIC_U=") and "MUSIC_U" not in cookie_header:
                    cookie_header = f"MUSIC_U={cookie_header}"
                headers["Cookie"] = cookie_header
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug(f"最近播放API返回code: {data.get('code')}, 数据条数: {len(data.get('data', {}).get('list', []))}")
                    if data.get("code") == 200:
                        for item in data.get("data", {}).get("list", [])[:limit]:
                            song_data = item.get("data", {})
                            artists = "/".join(
                                [a["name"] for a in song_data.get("ar", [])]
                            )
                            album_info = song_data.get("al", {})
                            song = Song(
                                title=song_data.get("name", ""),
                                artist=artists,
                                album=album_info.get("name", ""),
                                duration=song_data.get("dt", 0) // 1000,
                                song_id=str(song_data.get("id", "")),
                                source="netease",
                                cover_url=album_info.get("picUrl", ""),
                            )
                            # playTime 是毫秒时间戳
                            play_time = item.get("playTime", 0)
                            # 有些版本的API字段名不同，兼容处理
                            if not play_time:
                                play_time = item.get("time", 0)
                            results.append({
                                "song": song,
                                "play_time": play_time,
                            })
                    elif data.get("code") == 301:
                        logger.warning("网易云cookie无效或已过期 (code=301)，请重新绑定")
                    else:
                        logger.warning(f"获取最近播放返回异常code: {data.get('code')}, msg: {data.get('msg', '')}")
                else:
                    logger.warning(f"获取最近播放HTTP状态码: {resp.status}")
        except aiohttp.ClientConnectorError:
            logger.error("无法连接 NeteaseCloudMusicApi，获取最近播放失败")
        except Exception as e:
            logger.warning(f"获取最近播放记录失败: {e}")
        return results

    # ================================================================
    #  LRC 歌词解析工具
    # ================================================================

    @staticmethod
    def parse_lrc(lrc_text: str) -> List[Tuple[float, str]]:
        """解析 LRC 歌词，返回 [(秒数, 歌词文本), ...] 按时间排序

        支持格式: [mm:ss.xx] 歌词内容
        """
        if not lrc_text:
            return []
        pattern = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")
        result = []
        for line in lrc_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 一行可能有多个时间标签 [00:01.00][00:15.00]歌词
            timestamps = []
            text = line
            while True:
                m = pattern.match(text)
                if not m:
                    break
                minutes = int(m.group(1))
                seconds = int(m.group(2))
                ms_str = m.group(3) or "0"
                # 统一为毫秒
                if len(ms_str) == 2:
                    ms = int(ms_str) * 10
                elif len(ms_str) == 1:
                    ms = int(ms_str) * 100
                else:
                    ms = int(ms_str)
                total_seconds = minutes * 60 + seconds + ms / 1000.0
                timestamps.append(total_seconds)
                text = text[m.end():]
            text = text.strip()
            if text and timestamps:
                for ts in timestamps:
                    result.append((ts, text))
        result.sort(key=lambda x: x[0])
        return result

    @staticmethod
    def get_lyric_at_position(parsed_lrc: List[Tuple[float, str]], position_sec: float,
                              context: int = 2) -> dict:
        """根据播放进度获取当前歌词行及上下文

        Args:
            parsed_lrc: parse_lrc() 的返回值
            position_sec: 当前播放位置（秒）
            context: 上下文行数（前后各几行）

        Returns:
            dict: {
                "current_line": str,       # 当前歌词行
                "current_index": int,      # 当前行索引
                "context_before": [str],   # 前几行歌词
                "context_after": [str],    # 后几行歌词
                "progress_text": str,      # 格式化的进度文本
            }
        """
        if not parsed_lrc:
            return {
                "current_line": "",
                "current_index": -1,
                "context_before": [],
                "context_after": [],
                "progress_text": "",
            }

        # 找到当前播放位置对应的歌词行
        current_idx = 0
        for i, (ts, _) in enumerate(parsed_lrc):
            if ts <= position_sec:
                current_idx = i
            else:
                break

        current_line = parsed_lrc[current_idx][1]

        # 上下文
        before_start = max(0, current_idx - context)
        after_end = min(len(parsed_lrc), current_idx + context + 1)

        context_before = [parsed_lrc[i][1] for i in range(before_start, current_idx)]
        context_after = [parsed_lrc[i][1] for i in range(current_idx + 1, after_end)]

        # 格式化进度文本
        lines = []
        for i in range(before_start, after_end):
            prefix = ">> " if i == current_idx else "   "
            lines.append(f"{prefix}{parsed_lrc[i][1]}")
        progress_text = "\n".join(lines)

        return {
            "current_line": current_line,
            "current_index": current_idx,
            "context_before": context_before,
            "context_after": context_after,
            "progress_text": progress_text,
        }
