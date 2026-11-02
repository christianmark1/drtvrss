import ssl
from time import time
from aiohttp import ClientSession, TCPConnector
import certifi

from .drtv import get_jsonblob

ssl_context = ssl.create_default_context(cafile=certifi.where())


class LiveChannel:
    def __init__(self, title, stream_url):
        self.title = title
        self.stream_url = stream_url


live_channels = None
last_fetch = 0


async def get_channels():
    global live_channels
    global last_fetch
    if last_fetch + 60 * 30 < time() or (
        len(live_channels) == 0 and last_fetch + 60 < time()
    ):
        async with ClientSession(connector=TCPConnector(ssl=ssl_context)) as session:
            blob = await get_jsonblob(session, "https://www.dr.dk/drtv/kanal/dr1_20875")
            channels = {}
            # The page caches several lists (bookmarks, channels, ...) in no
            # particular order, so look through all of them for the channels
            for cached_list in blob["cache"]["list"].values():
                for channel in cached_list.get("list", {}).get("items", []):
                    if channel.get("type") != "channel":
                        continue
                    if "hlsURL" not in channel.get("customFields", {}):
                        continue
                    channels[channel["channelShortCode"]] = LiveChannel(
                        channel["title"], channel["customFields"]["hlsURL"]
                    )
            live_channels = channels
            last_fetch = time()
    return live_channels
