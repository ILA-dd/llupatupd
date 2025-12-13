import aiohttp
import logging
import json

class DDNetAPI:
    def __init__(self):
        self.master_url = "https://master1.ddnet.org/ddnet/15/servers.json"
        self.logger = logging.getLogger('ddnet_api')
        self.session = None

    async def init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def fetch_servers(self):
        """Fetches the server list from the DDNet master server."""
        if not self.session:
            await self.init_session()

        try:
            async with self.session.get(self.master_url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    self.logger.error(f"Failed to fetch servers: HTTP {response.status}")
                    return None
        except Exception as e:
            self.logger.error(f"Error fetching servers: {e}")
            return None

    def find_player(self, servers, player_name):
        """
        Searches for a player in the server list.
        Returns a list of dicts with server info for each instance of the player found.
        """
        found_instances = []
        if not servers or 'servers' not in servers:
            return found_instances

        target_name_lower = player_name.lower()

        for server in servers['servers']:
            # Correct path: server -> info -> clients
            if 'info' in server and 'clients' in server['info']:
                for client in server['info']['clients']:
                    if 'name' in client and client['name'].lower() == target_name_lower:
                        # Found the player
                        found_instances.append({
                            'player': client,
                            'server': server
                        })
        return found_instances
