"""
一起听歌 - AstrBot 音乐插件
支持多平台点歌、共享歌单、互动投票、音乐推荐与讨论
"""
import os
import time as _time
import logging
import random
import datetime
from typing import Dict

from astrbot.api.star import Context, Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record, Image

from .core.models import Song, SharedPlaylist, UserData
from .core.music_api import MusicAPI
from .core.storage import Storage

logger = logging.getLogger("astrbot_plugin_music_together")

# 切歌所需投票比例
SKIP_VOTE_RATIO = 0.5
# 搜索结果缓存 (session_id -> {user_id -> [Song]})
_search_cache: Dict[str, Dict[str, list]] = {}


class MusicTogetherPlugin(Star):
    """一起听歌插件"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        # AstrBot 传入的 config 是 AstrBotConfig (继承自 dict)，直接当 dict 用
        self.config = dict(config) if config else {}
        self.music_api = MusicAPI(self.config)

        # 数据目录
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(plugin_dir, "data")
        self.storage = Storage(data_dir)

        # 内存中的歌单缓存 {session_id: SharedPlaylist}
        self._playlists: Dict[str, SharedPlaylist] = {}

        # 默认音乐源
        self.default_source = self.config.get("default_source", "netease")
        # 发送模式: text / voice
        self.send_mode = self.config.get("send_mode", "text")
        # 切歌投票数
        self.skip_count = self.config.get("skip_vote_count", 2)

    async def initialize(self):
        # 检查 NeteaseCloudMusicApi 是否可用
        api_ok = await self.music_api.check_api_status()
        if api_ok:
            logger.info(f"一起听歌插件已加载 | NeteaseCloudMusicApi 连接成功 ({self.music_api.netease_api})")
        else:
            logger.warning(
                f"一起听歌插件已加载 | 无法连接 NeteaseCloudMusicApi ({self.music_api.netease_api})，"
                f"网易云相关功能将使用降级方案。请确认服务已启动。"
            )

    async def terminate(self):
        await self.music_api.close()
        # 保存所有歌单
        for playlist in self._playlists.values():
            self.storage.save_playlist(playlist)
        logger.info("一起听歌插件已卸载")

    def _get_playlist(self, session_id: str) -> SharedPlaylist:
        """获取或创建会话的共享歌单"""
        if session_id not in self._playlists:
            loaded = self.storage.load_playlist(session_id)
            if loaded:
                self._playlists[session_id] = loaded
            else:
                self._playlists[session_id] = SharedPlaylist(session_id=session_id)
        return self._playlists[session_id]

    def _get_user(self, user_id: str) -> UserData:
        """获取用户数据"""
        return self.storage.load_user(user_id)

    def _save_user(self, user_data: UserData):
        """保存用户数据"""
        self.storage.save_user(user_data)

    def _save_playlist(self, playlist: SharedPlaylist):
        """保存歌单"""
        self.storage.save_playlist(playlist)

    # ==================== 帮助命令 ====================

    @filter.command("听歌帮助", alias={"music_help", "听歌help"})
    async def cmd_help(self, event: AstrMessageEvent):
        """查看一起听歌帮助"""
        help_text = (
            "--- 一起听歌 ---\n"
            "\n"
            "[ 点歌 ]\n"
            "/点歌 <歌名> [歌手] - 搜索歌曲\n"
            "/选歌 <序号> - 从搜索结果中选择\n"
            "/音乐榜 - 查看热门排行榜\n"
            "\n"
            "[ 共享歌单 ]\n"
            "/歌单 - 查看当前共享歌单\n"
            "/加歌 <歌名> - 快速添加到歌单\n"
            "/加歌选 <序号> - 从搜索结果添加\n"
            "/删歌 <序号> - 从歌单删除\n"
            "/清空歌单 - 清空当前歌单\n"
            "\n"
            "[ 互动 ]\n"
            "/当前 - 查看当前播放\n"
            "/切歌 - 投票切换下一首\n"
            "/投票 <序号> - 投票想听的歌\n"
            "\n"
            "[ 歌词 & 评论 ]\n"
            "/歌词 - 查看当前歌曲歌词\n"
            "/歌词 <歌名> - 搜索歌词\n"
            "/热评 - 查看当前歌曲热门评论\n"
            "\n"
            "[ 个人 ]\n"
            "/收藏 - 收藏当前歌曲\n"
            "/我的收藏 - 查看收藏列表\n"
            "/取消收藏 <序号> - 取消收藏\n"
            "/听歌历史 - 查看播放历史\n"
            "\n"
            "[ 推荐 ]\n"
            "/推荐 - 根据历史推荐歌曲\n"
            "\n"
            "[ 系统 ]\n"
            "/绑定网易云 <cookie> - 绑定网易云账号(AI可感知你听的歌)\n"
            "/解绑网易云 - 解除绑定\n"
            "/音乐状态 - 查看API服务状态\n"
            "\n"
            "也可以直接和我聊音乐话题~"
        )
        yield event.plain_result(help_text)

    # ==================== 点歌功能 ====================

    @filter.command("点歌", alias={"搜歌", "search_music"})
    async def cmd_search(self, event: AstrMessageEvent):
        """搜索歌曲"""
        keyword = event.message_str.strip()
        if not keyword:
            yield event.plain_result("请输入歌名，例如: /点歌 晴天")
            return

        yield event.plain_result(f"正在搜索「{keyword}」...")

        songs = await self.music_api.search(keyword, source=self.default_source, limit=8)
        if not songs:
            yield event.plain_result(f"没有找到「{keyword}」相关的歌曲，换个关键词试试？")
            return

        # 缓存搜索结果
        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        if session_id not in _search_cache:
            _search_cache[session_id] = {}
        _search_cache[session_id][user_id] = songs

        # 构建结果列表
        lines = [f"搜索「{keyword}」结果:"]
        for i, song in enumerate(songs, 1):
            lines.append(song.display(i))
        lines.append("")
        lines.append("回复 /选歌 <序号> 播放")
        lines.append("回复 /加歌选 <序号> 添加到歌单")

        yield event.plain_result("\n".join(lines))

    @filter.command("选歌", alias={"选", "play"})
    async def cmd_select(self, event: AstrMessageEvent):
        """从搜索结果中选择歌曲播放"""
        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        user_name = event.get_sender_name() or user_id

        # 获取缓存的搜索结果
        cache = _search_cache.get(session_id, {}).get(user_id, [])
        if not cache:
            yield event.plain_result("没有搜索结果，请先使用 /点歌 <歌名> 搜索")
            return

        try:
            index = int(event.message_str.strip()) - 1
        except (ValueError, IndexError):
            yield event.plain_result("请输入正确的序号，例如: /选歌 1")
            return

        if index < 0 or index >= len(cache):
            yield event.plain_result(f"序号超出范围，请输入 1-{len(cache)}")
            return

        song = cache[index]

        # 获取播放链接
        url = await self.music_api.get_play_url(song)
        song.url = url

        # 记录播放历史
        user_data = self._get_user(user_id)
        user_data.add_to_history(song)
        self._save_user(user_data)

        # 更新歌单当前播放
        playlist = self._get_playlist(session_id)
        pos = playlist.add_song(song, user_id, user_name)
        playlist.current_index = pos - 1
        playlist.start_playing()
        self._save_playlist(playlist)

        # 发送歌曲信息
        async for result in self._send_song(event, song):
            yield result

    @filter.command("加歌选", alias={"addselect"})
    async def cmd_add_from_search(self, event: AstrMessageEvent):
        """从搜索结果中添加歌曲到歌单"""
        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        user_name = event.get_sender_name() or user_id

        cache = _search_cache.get(session_id, {}).get(user_id, [])
        if not cache:
            yield event.plain_result("没有搜索结果，请先使用 /点歌 <歌名> 搜索")
            return

        try:
            index = int(event.message_str.strip()) - 1
        except (ValueError, IndexError):
            yield event.plain_result("请输入正确的序号，例如: /加歌选 1")
            return

        if index < 0 or index >= len(cache):
            yield event.plain_result(f"序号超出范围，请输入 1-{len(cache)}")
            return

        song = cache[index]
        playlist = self._get_playlist(session_id)
        is_first = len(playlist.entries) == 0
        pos = playlist.add_song(song, user_id, user_name)
        if is_first:
            playlist.start_playing()
        self._save_playlist(playlist)

        yield event.plain_result(
            f"已添加到歌单第 {pos} 首:\n"
            f"{song.title} - {song.artist}"
        )

    # ==================== 共享歌单 ====================

    @filter.command("歌单", alias={"playlist", "查看歌单"})
    async def cmd_playlist(self, event: AstrMessageEvent):
        """查看当前共享歌单"""
        session_id = event.unified_msg_origin or ""
        playlist = self._get_playlist(session_id)

        if not playlist.entries:
            yield event.plain_result("当前歌单为空，使用 /加歌 <歌名> 添加歌曲吧！")
            return

        lines = ["--- 共享歌单 ---"]
        for i, entry in enumerate(playlist.entries):
            prefix = ">> " if i == playlist.current_index else "   "
            vote_str = f" [{entry.vote_count}票]" if entry.vote_count > 0 else ""
            lines.append(
                f"{prefix}{i + 1}. {entry.song.title} - {entry.song.artist}"
                f"{vote_str} (by {entry.added_by_name})"
            )

        current = playlist.current_song
        if current:
            lines.append(f"\n正在播放: {current.song.title} - {current.song.artist}")
        lines.append(f"\n共 {len(playlist.entries)} 首歌")

        yield event.plain_result("\n".join(lines))

    @filter.command("加歌", alias={"add", "添加"})
    async def cmd_add_song(self, event: AstrMessageEvent):
        """快速搜索并添加歌曲到歌单"""
        keyword = event.message_str.strip()
        if not keyword:
            yield event.plain_result("请输入歌名，例如: /加歌 晴天")
            return

        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        user_name = event.get_sender_name() or user_id

        songs = await self.music_api.search(keyword, source=self.default_source, limit=1)
        if not songs:
            yield event.plain_result(f"没有找到「{keyword}」，换个关键词试试？")
            return

        song = songs[0]
        playlist = self._get_playlist(session_id)
        is_first = len(playlist.entries) == 0
        pos = playlist.add_song(song, user_id, user_name)
        # 如果是歌单第一首歌，自动开始播放
        if is_first:
            playlist.start_playing()
        self._save_playlist(playlist)

        yield event.plain_result(
            f"已添加到歌单第 {pos} 首:\n"
            f"{song.title} - {song.artist}\n"
            f"当前歌单共 {len(playlist.entries)} 首歌"
        )

    @filter.command("删歌", alias={"remove", "删除"})
    async def cmd_remove_song(self, event: AstrMessageEvent):
        """从歌单删除歌曲"""
        session_id = event.unified_msg_origin or ""
        playlist = self._get_playlist(session_id)

        try:
            index = int(event.message_str.strip()) - 1
        except (ValueError, IndexError):
            yield event.plain_result("请输入正确的序号，例如: /删歌 1")
            return

        if index < 0 or index >= len(playlist.entries):
            yield event.plain_result(f"序号超出范围，请输入 1-{len(playlist.entries)}")
            return

        removed = playlist.entries.pop(index)
        # 调整当前播放索引
        if index < playlist.current_index:
            playlist.current_index -= 1
        elif index == playlist.current_index:
            if playlist.current_index >= len(playlist.entries):
                playlist.current_index = max(0, len(playlist.entries) - 1)
        self._save_playlist(playlist)

        yield event.plain_result(
            f"已从歌单移除: {removed.song.title} - {removed.song.artist}"
        )

    @filter.command("清空歌单", alias={"clear_playlist"})
    async def cmd_clear_playlist(self, event: AstrMessageEvent):
        """清空当前歌单"""
        session_id = event.unified_msg_origin or ""
        playlist = self._get_playlist(session_id)
        playlist.entries.clear()
        playlist.current_index = 0
        playlist.skip_votes.clear()
        self._save_playlist(playlist)
        yield event.plain_result("歌单已清空！")

    # ==================== 互动功能 ====================

    @filter.command("当前", alias={"now", "正在播放", "nowplaying"})
    async def cmd_now_playing(self, event: AstrMessageEvent):
        """查看当前播放的歌曲"""
        session_id = event.unified_msg_origin or ""
        playlist = self._get_playlist(session_id)
        current = playlist.current_song

        if not current:
            yield event.plain_result("当前没有播放歌曲，使用 /点歌 或 /加歌 开始吧！")
            return

        song = current.song
        lines = [
            "--- 正在播放 ---",
            f"  {song.title}",
            f"  {song.artist}",
        ]
        if song.album:
            lines.append(f"  专辑: {song.album}")
        if song.duration > 0:
            m, s = divmod(song.duration, 60)
            lines.append(f"  时长: {m}:{s:02d}")

        src_map = {"netease": "网易云", "qqmusic": "QQ音乐", "kugou": "酷狗"}
        lines.append(f"  来源: {src_map.get(song.source, song.source)}")
        lines.append(f"  点歌人: {current.added_by_name}")

        pos = playlist.current_index + 1
        total = len(playlist.entries)
        lines.append(f"\n  [{pos}/{total}]")

        if playlist.current_index < total - 1:
            next_entry = playlist.entries[playlist.current_index + 1]
            lines.append(f"  下一首: {next_entry.song.title} - {next_entry.song.artist}")

        yield event.plain_result("\n".join(lines))

    @filter.command("切歌", alias={"skip", "下一首", "next"})
    async def cmd_skip(self, event: AstrMessageEvent):
        """投票切歌"""
        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        playlist = self._get_playlist(session_id)

        if not playlist.current_song:
            yield event.plain_result("当前没有播放歌曲")
            return

        vote_count = playlist.vote_skip(user_id)

        # 点歌人自己可直接切，或达到投票数
        current = playlist.current_song
        is_owner = (current.added_by == user_id)

        need = self.skip_count
        if is_owner or vote_count >= need:
            next_song = playlist.next_song()
            self._save_playlist(playlist)
            if next_song:
                # 记录播放历史
                user_data = self._get_user(user_id)
                user_data.add_to_history(next_song.song)
                self._save_user(user_data)

                yield event.plain_result(
                    f"切歌成功！\n"
                    f"正在播放: {next_song.song.title} - {next_song.song.artist}"
                )
                # 发送歌曲
                url = await self.music_api.get_play_url(next_song.song)
                next_song.song.url = url
                async for result in self._send_song(event, next_song.song):
                    yield result
            else:
                yield event.plain_result("歌单已播完，没有下一首了！")
        else:
            yield event.plain_result(
                f"切歌投票 {vote_count}/{need}\n"
                f"还需要 {need - vote_count} 票"
            )

    @filter.command("投票", alias={"vote"})
    async def cmd_vote(self, event: AstrMessageEvent):
        """投票想听的歌"""
        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        playlist = self._get_playlist(session_id)

        try:
            index = int(event.message_str.strip()) - 1
        except (ValueError, IndexError):
            yield event.plain_result("请输入正确的序号，例如: /投票 3")
            return

        if playlist.vote_song(index, user_id):
            entry = playlist.entries[index]
            self._save_playlist(playlist)
            yield event.plain_result(
                f"已为「{entry.song.title}」投票！当前 {entry.vote_count} 票"
            )
        else:
            yield event.plain_result(f"序号超出范围，请输入 1-{len(playlist.entries)}")

    # ==================== 歌词功能 ====================

    @filter.command("歌词", alias={"lyric", "lyrics"})
    async def cmd_lyric(self, event: AstrMessageEvent):
        """查看歌词"""
        keyword = event.message_str.strip()
        session_id = event.unified_msg_origin or ""

        if not keyword:
            # 获取当前播放歌曲的歌词
            playlist = self._get_playlist(session_id)
            current = playlist.current_song
            if not current:
                yield event.plain_result("当前没有播放歌曲，请指定歌名: /歌词 <歌名>")
                return
            song = current.song
        else:
            # 搜索歌曲获取歌词
            songs = await self.music_api.search(keyword, source="netease", limit=1)
            if not songs:
                yield event.plain_result(f"没有找到「{keyword}」的歌词")
                return
            song = songs[0]

        lyric = await self.music_api.get_lyric(song)
        if not lyric:
            yield event.plain_result(f"暂无「{song.title}」的歌词")
            return

        # 解析LRC歌词，去掉时间标签
        lines = []
        for line in lyric.split("\n"):
            # 去掉 [xx:xx.xx] 格式的时间标签
            text = line.strip()
            while text.startswith("[") and "]" in text:
                text = text[text.index("]") + 1:]
            text = text.strip()
            if text:
                lines.append(text)

        if not lines:
            yield event.plain_result(f"暂无「{song.title}」的歌词")
            return

        # 限制歌词长度
        max_lines = 30
        lyric_text = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            lyric_text += f"\n\n... 共 {len(lines)} 行，仅显示前 {max_lines} 行"

        yield event.plain_result(
            f"--- {song.title} - {song.artist} ---\n\n{lyric_text}"
        )

    # ==================== 热评功能 ====================

    @filter.command("热评", alias={"comments", "评论"})
    async def cmd_hot_comments(self, event: AstrMessageEvent):
        """查看当前歌曲热门评论"""
        keyword = event.message_str.strip()
        session_id = event.unified_msg_origin or ""

        if not keyword:
            playlist = self._get_playlist(session_id)
            current = playlist.current_song
            if not current:
                yield event.plain_result("当前没有播放歌曲，请指定歌名: /热评 <歌名>")
                return
            song = current.song
        else:
            songs = await self.music_api.search(keyword, source="netease", limit=1)
            if not songs:
                yield event.plain_result(f"没有找到「{keyword}」")
                return
            song = songs[0]

        if song.source != "netease":
            yield event.plain_result("热评功能仅支持网易云音乐歌曲")
            return

        comments = await self.music_api.get_netease_hot_comments(song.song_id, limit=5)
        if not comments:
            yield event.plain_result(f"暂无「{song.title}」的热门评论")
            return

        lines = [f"--- {song.title} 热门评论 ---", ""]
        for i, comment in enumerate(comments, 1):
            lines.append(f"{i}. {comment}")
            lines.append("")

        yield event.plain_result("\n".join(lines))

    # ==================== API 状态 ====================

    @filter.command("音乐状态", alias={"music_status"})
    async def cmd_status(self, event: AstrMessageEvent):
        """检查音乐API服务状态"""
        api_ok = await self.music_api.check_api_status()
        api_url = self.music_api.netease_api
        has_cookie = bool(self.music_api.netease_cookie)
        quality = self.config.get("music_quality", "standard")

        quality_map = {
            "standard": "标准",
            "higher": "较高",
            "exhigh": "极高",
            "lossless": "无损",
            "hires": "Hi-Res",
        }

        lines = [
            "--- 音乐服务状态 ---",
            f"NeteaseCloudMusicApi: {'正常' if api_ok else '无法连接'}",
            f"API 地址: {api_url}",
            f"登录状态: {'已登录' if has_cookie else '未登录 (部分功能受限)'}",
            f"音质设置: {quality_map.get(quality, quality)}",
            f"默认音乐源: {self.default_source}",
            f"发送模式: {self.send_mode}",
        ]

        if not api_ok:
            lines.append("")
            lines.append("请确认 NeteaseCloudMusicApi 服务已启动")
            lines.append("部署方式: npx NeteaseCloudMusicApi")
            lines.append("或 Docker: docker run -p 3000:3000 binaryify/netease_cloud_music_api")

        yield event.plain_result("\n".join(lines))

    # ==================== 网易云账号绑定 ====================

    @filter.command("绑定网易云", alias={"bind_netease", "绑定cookie"})
    async def cmd_bind_netease(self, event: AstrMessageEvent, cookie: str = ""):
        """绑定网易云音乐账号cookie，绑定后AI可以感知你在网易云听的歌"""
        if not cookie:
            yield event.plain_result(
                "请提供你的网易云cookie，格式:\n"
                "/绑定网易云 你的MUSIC_U值\n"
                "\n"
                "获取方式:\n"
                "1. 浏览器登录 music.163.com\n"
                "2. F12 -> Application -> Cookies\n"
                "3. 找到 MUSIC_U，复制它的值\n"
                "\n"
                "绑定后AI就能知道你在网易云听什么歌了！\n"
                "建议私聊发送，避免泄露。"
            )
            return

        user_id = event.get_sender_id() or ""

        # 自动补全cookie格式
        if "MUSIC_U" not in cookie:
            cookie = f"MUSIC_U={cookie}"
        # 如果用户粘贴了完整的cookie字符串（含多个字段），提取MUSIC_U部分
        if ";" in cookie:
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("MUSIC_U="):
                    cookie = part
                    break

        user_data = self._get_user(user_id)
        user_data.netease_cookie = cookie
        self._save_user(user_data)

        # 验证cookie是否有效
        yield event.plain_result("正在验证cookie...")
        try:
            recent = await self.music_api.get_recent_songs(limit=1, cookie=cookie)
        except Exception as e:
            logger.warning(f"验证cookie时出错: {e}")
            recent = []

        if recent:
            song = recent[0]["song"]
            yield event.plain_result(
                f"绑定成功！\n"
                f"你最近听的: {song.title} - {song.artist}\n"
                f"\n现在AI可以感知你在网易云听的歌了~"
            )
        else:
            yield event.plain_result(
                "已保存cookie，但验证未通过。可能原因:\n"
                "1. MUSIC_U值不对 (确认复制完整)\n"
                "2. cookie已过期 (重新登录获取)\n"
                "3. NeteaseCloudMusicApi 未启动\n"
                "\n可用 /音乐状态 检查API连接"
            )

    @filter.command("解绑网易云", alias={"unbind_netease", "解绑cookie"})
    async def cmd_unbind_netease(self, event: AstrMessageEvent):
        """解除网易云音乐账号绑定"""
        user_id = event.get_sender_id() or ""
        user_data = self._get_user(user_id)
        if user_data.netease_cookie:
            user_data.netease_cookie = ""
            self._save_user(user_data)
            yield event.plain_result("已解除网易云账号绑定。")
        else:
            yield event.plain_result("你还没有绑定网易云账号。")

    # ==================== 个人功能 ====================

    @filter.command("收藏", alias={"fav", "favorite"})
    async def cmd_favorite(self, event: AstrMessageEvent):
        """收藏当前歌曲"""
        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        playlist = self._get_playlist(session_id)
        current = playlist.current_song

        if not current:
            yield event.plain_result("当前没有播放歌曲，无法收藏")
            return

        user_data = self._get_user(user_id)
        if user_data.add_favorite(current.song):
            self._save_user(user_data)
            yield event.plain_result(
                f"已收藏: {current.song.title} - {current.song.artist}\n"
                f"收藏总数: {len(user_data.favorites)}"
            )
        else:
            yield event.plain_result("这首歌已经在收藏里了")

    @filter.command("我的收藏", alias={"myfav", "my_favorites"})
    async def cmd_my_favorites(self, event: AstrMessageEvent):
        """查看个人收藏"""
        user_id = event.get_sender_id() or ""
        user_data = self._get_user(user_id)

        if not user_data.favorites:
            yield event.plain_result("你还没有收藏任何歌曲，播放歌曲时使用 /收藏 添加")
            return

        lines = ["--- 我的收藏 ---"]
        for i, fav in enumerate(user_data.favorites, 1):
            song = Song.from_dict(fav)
            lines.append(f"{i}. {song.title} - {song.artist}")

        lines.append(f"\n共 {len(user_data.favorites)} 首")
        lines.append("使用 /取消收藏 <序号> 移除")

        yield event.plain_result("\n".join(lines))

    @filter.command("取消收藏", alias={"unfav", "remove_fav"})
    async def cmd_remove_favorite(self, event: AstrMessageEvent):
        """取消收藏"""
        user_id = event.get_sender_id() or ""
        user_data = self._get_user(user_id)

        try:
            index = int(event.message_str.strip()) - 1
        except (ValueError, IndexError):
            yield event.plain_result("请输入正确的序号，例如: /取消收藏 1")
            return

        if user_data.remove_favorite(index):
            self._save_user(user_data)
            yield event.plain_result("已取消收藏")
        else:
            yield event.plain_result(f"序号超出范围，请输入 1-{len(user_data.favorites)}")

    @filter.command("听歌历史", alias={"history", "播放历史"})
    async def cmd_history(self, event: AstrMessageEvent):
        """查看播放历史"""
        user_id = event.get_sender_id() or ""
        user_data = self._get_user(user_id)

        if not user_data.play_history:
            yield event.plain_result("还没有播放记录")
            return

        # 显示最近10条
        recent = user_data.play_history[-10:]
        recent.reverse()

        lines = ["--- 最近播放 ---"]
        for i, h in enumerate(recent, 1):
            lines.append(f"{i}. {h['title']} - {h['artist']}")

        # 显示最常听
        top = user_data.get_top_songs(5)
        if top:
            lines.append("\n--- 最常听 ---")
            for i, item in enumerate(top, 1):
                s = item["song"]
                lines.append(f"{i}. {s['title']} - {s['artist']} (听了{item['count']}次)")

        yield event.plain_result("\n".join(lines))

    # ==================== 排行榜 ====================

    @filter.command("音乐榜", alias={"hot", "排行榜", "热歌榜"})
    async def cmd_hot(self, event: AstrMessageEvent):
        """查看热门排行榜"""
        yield event.plain_result("正在获取热歌榜...")

        songs = await self.music_api.get_hot_songs()
        if not songs:
            yield event.plain_result("获取排行榜失败，请稍后再试")
            return

        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        if session_id not in _search_cache:
            _search_cache[session_id] = {}
        _search_cache[session_id][user_id] = songs

        lines = ["--- 热歌榜 TOP20 ---"]
        for i, song in enumerate(songs[:20], 1):
            lines.append(f"{i}. {song.title} - {song.artist}")
        lines.append("\n回复 /选歌 <序号> 播放")

        yield event.plain_result("\n".join(lines))

    # ==================== 推荐功能 ====================

    @filter.command("推荐", alias={"recommend", "猜你喜欢"})
    async def cmd_recommend(self, event: AstrMessageEvent):
        """根据历史推荐歌曲"""
        user_id = event.get_sender_id() or ""
        user_data = self._get_user(user_id)

        if not user_data.play_history:
            # 没有历史，推荐热门
            yield event.plain_result("你还没有播放记录，为你推荐热门歌曲...")
            songs = await self.music_api.get_hot_songs()
            if songs:
                # 随机选5首
                picks = random.sample(songs, min(5, len(songs)))
                session_id = event.unified_msg_origin or ""
                if session_id not in _search_cache:
                    _search_cache[session_id] = {}
                _search_cache[session_id][user_id] = picks

                lines = ["--- 为你推荐 ---"]
                for i, song in enumerate(picks, 1):
                    lines.append(song.display(i))
                lines.append("\n回复 /选歌 <序号> 播放")
                yield event.plain_result("\n".join(lines))
            return

        # 基于历史推荐：取最常听的歌手，搜索相关歌曲
        top = user_data.get_top_songs(3)
        if not top:
            yield event.plain_result("播放记录不足，多听几首再来推荐吧！")
            return

        yield event.plain_result("正在根据你的口味推荐...")

        all_songs = []
        for item in top:
            artist = item["song"]["artist"].split("/")[0]
            songs = await self.music_api.search(artist, source=self.default_source, limit=5)
            all_songs.extend(songs)

        if not all_songs:
            yield event.plain_result("推荐失败，请稍后再试")
            return

        # 去重并随机选取
        seen = set()
        unique = []
        for s in all_songs:
            key = f"{s.title}_{s.artist}"
            if key not in seen:
                seen.add(key)
                unique.append(s)

        picks = random.sample(unique, min(8, len(unique)))

        session_id = event.unified_msg_origin or ""
        if session_id not in _search_cache:
            _search_cache[session_id] = {}
        _search_cache[session_id][user_id] = picks

        lines = ["--- 猜你喜欢 ---"]
        for i, song in enumerate(picks, 1):
            lines.append(song.display(i))
        lines.append("\n回复 /选歌 <序号> 播放")
        lines.append("回复 /加歌选 <序号> 添加到歌单")

        yield event.plain_result("\n".join(lines))

    # ==================== LLM Tool 集成 ====================

    @filter.llm_tool()
    async def search_and_play_music(self, event: AstrMessageEvent, song_name: str):
        """当用户想听歌、点歌、搜索音乐时调用此工具。搜索歌曲并返回结果。

        Args:
            song_name(string): 歌曲名称或关键词，可以包含歌手名
        """
        songs = await self.music_api.search(song_name, source=self.default_source, limit=5)
        if not songs:
            return f"没有找到「{song_name}」相关的歌曲"

        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        if session_id not in _search_cache:
            _search_cache[session_id] = {}
        _search_cache[session_id][user_id] = songs

        lines = [f"找到以下歌曲:"]
        for i, song in enumerate(songs, 1):
            lines.append(song.display(i))
        lines.append("\n用户可以回复 /选歌 <序号> 来播放")
        return "\n".join(lines)

    @filter.llm_tool()
    async def recommend_music_by_mood(self, event: AstrMessageEvent, mood: str):
        """当用户描述心情或想要某种风格的音乐推荐时调用此工具。

        Args:
            mood(string): 用户的心情或想要的音乐风格，如"开心"、"伤感"、"摇滚"、"安静"等
        """
        # 根据心情映射搜索关键词
        mood_keywords = {
            "开心": "欢快 快乐",
            "伤感": "伤感 难过",
            "安静": "轻音乐 纯音乐",
            "摇滚": "摇滚 rock",
            "古风": "古风 中国风",
            "电子": "电子 EDM",
            "说唱": "说唱 rap",
            "民谣": "民谣",
            "爵士": "爵士 jazz",
            "浪漫": "浪漫 情歌",
        }

        keyword = mood_keywords.get(mood, mood)
        songs = await self.music_api.search(keyword, source=self.default_source, limit=5)

        if not songs:
            return f"没有找到适合「{mood}」心情的歌曲"

        session_id = event.unified_msg_origin or ""
        user_id = event.get_sender_id() or ""
        if session_id not in _search_cache:
            _search_cache[session_id] = {}
        _search_cache[session_id][user_id] = songs

        lines = [f"根据「{mood}」心情推荐:"]
        for i, song in enumerate(songs, 1):
            lines.append(song.display(i))
        lines.append("\n用户可以回复 /选歌 <序号> 来播放")
        return "\n".join(lines)

    @filter.llm_tool()
    async def get_song_lyrics(self, event: AstrMessageEvent, song_name: str):
        """当用户想查看歌词时调用此工具。

        Args:
            song_name(string): 歌曲名称
        """
        songs = await self.music_api.search(song_name, source="netease", limit=1)
        if not songs:
            return f"没有找到「{song_name}」的歌词"

        song = songs[0]
        lyric = await self.music_api.get_lyric(song)
        if not lyric:
            return f"暂无「{song.title}」的歌词"

        # 解析歌词
        lines = []
        for line in lyric.split("\n"):
            text = line.strip()
            while text.startswith("[") and "]" in text:
                text = text[text.index("]") + 1:]
            text = text.strip()
            if text:
                lines.append(text)

        return f"「{song.title} - {song.artist}」歌词:\n" + "\n".join(lines[:20])

    async def _get_user_cookie(self, user_id: str) -> str:
        """获取用户的网易云cookie（优先用户绑定的，其次全局配置的）"""
        user_data = self._get_user(user_id)
        return user_data.netease_cookie or self.music_api.netease_cookie

    async def _fetch_netease_now_playing(self, cookie: str) -> dict:
        """从网易云拉取用户最近播放的第一首歌，作为"当前在听的歌"

        Returns:
            dict: {"song": Song, "play_time": int(ms), "elapsed_estimate": float(秒),
                   "is_stale": bool, "elapsed_since_record": float(秒)} 或空dict
        """
        if not cookie:
            return {}
        try:
            recent = await self.music_api.get_recent_songs(limit=3, cookie=cookie)
            if not recent:
                logger.debug("网易云最近播放返回空列表")
                return {}
            item = recent[0]
            song = item["song"]
            play_time_ms = item.get("play_time", 0)

            logger.debug(f"网易云最近播放: {song.title} - {song.artist}, playTime={play_time_ms}, duration={song.duration}s")

            if play_time_ms > 0:
                now = _time.time()
                elapsed_since_record = now - play_time_ms / 1000.0

                # 10分钟内的记录都认为是"可能正在听"
                # 因为网易云API有延迟，而且用户可能在循环播放
                is_stale = elapsed_since_record > 600  # 超过10分钟算过期

                # 估算歌曲内的播放位置
                if song.duration > 0:
                    if elapsed_since_record <= 0:
                        elapsed_in_song = 0.0
                    elif elapsed_since_record <= song.duration:
                        # 还在第一遍播放中
                        elapsed_in_song = elapsed_since_record
                    else:
                        # 可能在循环播放，取模
                        elapsed_in_song = elapsed_since_record % song.duration
                else:
                    elapsed_in_song = elapsed_since_record

                return {
                    "song": song,
                    "play_time": play_time_ms,
                    "elapsed_estimate": max(0, elapsed_in_song),
                    "elapsed_since_record": elapsed_since_record,
                    "is_stale": is_stale,
                }
            # 没有播放时间，但有歌曲信息，也返回（标记为stale但仍然有用）
            return {
                "song": song,
                "play_time": 0,
                "elapsed_estimate": 0,
                "elapsed_since_record": 0,
                "is_stale": True,
            }
        except Exception as e:
            logger.warning(f"拉取网易云最近播放失败: {e}")
            return {}

    @filter.llm_tool()
    async def get_current_playing(self, event: AstrMessageEvent):
        """当用户提到"这首歌"、"现在听的"、"当前播放"、"听到哪了"、"现在什么歌词"、"我在听"或聊到正在听的音乐时调用此工具。
        获取用户真实正在听的歌曲信息，包括实时播放进度、当前歌词行。
        优先从用户绑定的网易云账号获取真实播放状态，也会参考插件内歌单。
        """
        user_id = event.get_sender_id() or ""
        session_id = event.unified_msg_origin or ""
        cookie = await self._get_user_cookie(user_id)

        lines = []
        song = None
        elapsed = 0.0
        has_progress = False
        source_desc = ""

        # ===== 优先尝试从网易云获取真实播放状态 =====
        netease_info = await self._fetch_netease_now_playing(cookie)
        if netease_info and netease_info.get("song"):
            is_stale = netease_info.get("is_stale", True)
            song = netease_info["song"]
            elapsed = netease_info.get("elapsed_estimate", 0)
            has_progress = not is_stale and elapsed > 0
            elapsed_since = netease_info.get("elapsed_since_record", 0)

            play_time_ms = netease_info.get("play_time", 0)
            time_str = ""
            if play_time_ms:
                dt = datetime.datetime.fromtimestamp(play_time_ms / 1000)
                time_str = dt.strftime('%H:%M:%S')

            if not is_stale:
                source_desc = f"来源: 网易云音乐 (真实播放)"
                if time_str:
                    source_desc += f"\n开始播放时间: {time_str}"
                lines.append("用户当前真实正在听的歌曲 (来自网易云音乐):")
            else:
                # 超过10分钟，但仍然是最近听的歌，告诉AI
                ago_min = int(elapsed_since / 60)
                source_desc = f"来源: 网易云音乐 (约{ago_min}分钟前播放)"
                lines.append(f"用户最近在网易云听的歌 (约{ago_min}分钟前):")

        # ===== 如果网易云完全没数据，回退到插件歌单 =====
        if song is None:
            playlist = self._get_playlist(session_id)
            current = playlist.current_song
            if current:
                song = current.song
                elapsed = current.playback_seconds
                has_progress = current.started_at > 0
                source_desc = f"来源: 插件歌单 (由 {current.added_by_name} 点歌)"
                lines.append("当前插件歌单正在播放的歌曲:")

                # 歌单位置
                pos = playlist.current_index + 1
                total = len(playlist.entries)
                lines.append(f"歌单位置: 第{pos}首/共{total}首")
                if playlist.current_index < total - 1:
                    next_entry = playlist.entries[playlist.current_index + 1]
                    lines.append(f"下一首: {next_entry.song.title} - {next_entry.song.artist}")

        if song is None:
            if not cookie:
                return "当前没有播放信息。用户还没有点歌，也没有绑定网易云账号。可以使用 /绑定网易云 <cookie> 绑定账号后，AI就能感知用户在网易云听的歌了。"
            return "当前没有播放信息。用户没有在插件中点歌，网易云也没有最近的播放记录。可能cookie已过期，建议用 /绑定网易云 重新绑定。"

        # ===== 歌曲基本信息 =====
        lines.append(f"歌名: {song.title}")
        lines.append(f"歌手: {song.artist}")
        if song.album:
            lines.append(f"专辑: {song.album}")
        if song.duration > 0:
            m, s = divmod(song.duration, 60)
            lines.append(f"总时长: {m}分{s:02d}秒")
        lines.append(source_desc)

        # ===== 播放进度 =====
        if has_progress:
            em, es = divmod(int(elapsed), 60)
            if song.duration > 0:
                dm, ds = divmod(song.duration, 60)
                progress_pct = min(100, int(elapsed / song.duration * 100))
                lines.append(f"播放进度: {em}:{es:02d} / {dm}:{ds:02d} ({progress_pct}%)")
                if elapsed >= song.duration:
                    lines.append("状态: 可能已播放完毕或在循环播放")
                else:
                    lines.append("状态: 正在播放中")
            else:
                lines.append(f"已播放约: {em}:{es:02d}")

        # ===== 歌词定位 =====
        try:
            lyric_raw = await self.music_api.get_lyric(song)
            if lyric_raw:
                parsed_lrc = MusicAPI.parse_lrc(lyric_raw)
                if parsed_lrc and has_progress:
                    lyric_info = MusicAPI.get_lyric_at_position(parsed_lrc, elapsed, context=3)
                    if lyric_info["current_line"]:
                        lines.append(f"\n当前正在唱的歌词: 「{lyric_info['current_line']}」")
                        lines.append(f"\n歌词上下文 (>> 标记当前行):")
                        lines.append(lyric_info["progress_text"])
                elif parsed_lrc:
                    lines.append(f"\n歌词开头:")
                    for _, text in parsed_lrc[:6]:
                        lines.append(f"  {text}")
                else:
                    plain_lines = [l.strip() for l in lyric_raw.split("\n") if l.strip()]
                    if plain_lines:
                        lines.append(f"\n歌词片段:")
                        for l in plain_lines[:8]:
                            lines.append(f"  {l}")
        except Exception:
            pass

        return "\n".join(lines)

    @filter.llm_tool()
    async def get_user_music_profile(self, event: AstrMessageEvent):
        """当用户聊到自己的音乐喜好、听歌习惯，或者需要了解用户音乐口味来做推荐和聊天时调用此工具。
        获取用户的听歌画像，包括最近播放、最常听的歌、收藏列表。
        """
        user_id = event.get_sender_id() or ""
        user_data = self._get_user(user_id)

        lines = [f"用户的音乐画像:"]

        # 最近播放
        if user_data.play_history:
            recent = user_data.play_history[-5:]
            recent.reverse()
            lines.append("\n最近播放:")
            for i, h in enumerate(recent, 1):
                lines.append(f"  {i}. {h['title']} - {h['artist']}")
        else:
            lines.append("\n最近播放: 暂无播放记录")

        # 最常听
        top = user_data.get_top_songs(5)
        if top:
            lines.append("\n最常听的歌:")
            for i, item in enumerate(top, 1):
                s = item["song"]
                lines.append(f"  {i}. {s['title']} - {s['artist']} (听了{item['count']}次)")

        # 收藏
        if user_data.favorites:
            lines.append(f"\n收藏列表 (共{len(user_data.favorites)}首):")
            for i, fav in enumerate(user_data.favorites[:5], 1):
                lines.append(f"  {i}. {fav['title']} - {fav['artist']}")
            if len(user_data.favorites) > 5:
                lines.append(f"  ... 还有{len(user_data.favorites) - 5}首")
        else:
            lines.append("\n收藏列表: 暂无收藏")

        # 总结听歌偏好
        if top:
            artists = set()
            for item in top:
                for a in item["song"]["artist"].split("/"):
                    artists.add(a.strip())
            if artists:
                lines.append(f"\n常听歌手: {', '.join(list(artists)[:5])}")

        return "\n".join(lines)

    @filter.llm_tool()
    async def get_playlist_info(self, event: AstrMessageEvent):
        """当用户问到歌单、播放列表、接下来放什么歌、或者想了解当前群里的共享歌单时调用此工具。
        获取当前会话的共享歌单信息。
        """
        session_id = event.unified_msg_origin or ""
        playlist = self._get_playlist(session_id)

        if not playlist.entries:
            return "当前共享歌单为空，还没有人添加歌曲。"

        lines = [f"当前共享歌单 (共{len(playlist.entries)}首):"]

        for i, entry in enumerate(playlist.entries):
            prefix = ">> " if i == playlist.current_index else "   "
            playing = " [正在播放]" if i == playlist.current_index else ""
            vote_str = f" [{entry.vote_count}票]" if entry.vote_count > 0 else ""
            lines.append(
                f"{prefix}{i + 1}. {entry.song.title} - {entry.song.artist}"
                f"{playing}{vote_str} (by {entry.added_by_name})"
            )

        current = playlist.current_song
        if current:
            lines.append(f"\n正在播放: {current.song.title} - {current.song.artist}")
            remaining = len(playlist.entries) - playlist.current_index - 1
            lines.append(f"剩余待播: {remaining}首")

        return "\n".join(lines)

    @filter.llm_tool()
    async def get_netease_recent_plays(self, event: AstrMessageEvent):
        """当用户提到自己在网易云音乐上听了什么、最近在听什么歌、或者想知道自己网易云的播放记录时调用此工具。
        获取用户在网易云音乐上的真实最近播放记录（需要用户绑定网易云cookie）。
        """
        user_id = event.get_sender_id() or ""
        cookie = await self._get_user_cookie(user_id)

        recent = await self.music_api.get_recent_songs(limit=10, cookie=cookie)
        if not recent:
            if not cookie:
                return "用户未绑定网易云账号，无法获取播放记录。请使用 /绑定网易云 <cookie> 绑定后即可。"
            return "获取网易云最近播放记录失败，可能是cookie已过期或网络问题。"

        # 缓存搜索结果以便用户可以选歌
        session_id = event.unified_msg_origin or ""
        if session_id not in _search_cache:
            _search_cache[session_id] = {}
        _search_cache[session_id][user_id] = [item["song"] for item in recent]

        lines = ["用户在网易云音乐上的最近播放记录:"]
        for i, item in enumerate(recent, 1):
            song = item["song"]
            play_time = item.get("play_time", 0)
            time_str = ""
            if play_time:
                dt = datetime.datetime.fromtimestamp(play_time / 1000)
                time_str = f" ({dt.strftime('%m-%d %H:%M')})"
            duration_str = ""
            if song.duration > 0:
                m, s = divmod(song.duration, 60)
                duration_str = f" [{m}:{s:02d}]"
            lines.append(f"  {i}. {song.title} - {song.artist}{duration_str}{time_str}")

        lines.append("\n用户可以回复 /选歌 <序号> 来播放这些歌曲")
        return "\n".join(lines)

    # ==================== 辅助方法 ====================

    async def _send_song(self, event: AstrMessageEvent, song: Song):
        """发送歌曲信息（async 生成器）"""
        src_map = {"netease": "网易云", "qqmusic": "QQ音乐", "kugou": "酷狗"}
        source_name = src_map.get(song.source, song.source)

        lines = [
            f"正在播放: {song.title} - {song.artist}",
        ]
        if song.album:
            lines.append(f"专辑: {song.album}")
        if song.duration > 0:
            m, s = divmod(song.duration, 60)
            lines.append(f"时长: {m}:{s:02d}")
        lines.append(f"来源: {source_name}")

        if song.url:
            lines.append(f"链接: {song.url}")

        yield event.plain_result("\n".join(lines))

        # 如果有封面图，发送封面
        if song.cover_url:
            try:
                chain = event.make_result()
                chain.chain.append(Image(file=song.cover_url))
                yield chain
            except Exception as e:
                logger.debug(f"发送封面失败: {e}")

        # 如果配置了语音模式且有URL，尝试发送语音
        if self.send_mode == "voice" and song.url:
            try:
                chain = event.make_result()
                chain.chain.append(Record(file=song.url))
                yield chain
            except Exception as e:
                logger.debug(f"发送语音失败: {e}")
