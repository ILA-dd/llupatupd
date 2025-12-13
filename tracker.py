import logging
from datetime import datetime

class PlayerTracker:
    def __init__(self, api_client):
        self.api = api_client
        self.tracked_players = set()
        # Stores the last known state of a player: {player_name: { 'server_addr': '...', 'timestamp': ... }}
        self.last_known_state = {}
        self.logger = logging.getLogger('tracker')

    def add_player(self, player_name):
        self.tracked_players.add(player_name)

    def remove_player(self, player_name):
        # We need to find the name in the set case-insensitively or just require exact match?
        # Standard usability suggests case-insensitive remove, but for storage we keep original.
        # Let's try to remove exact match first, then case-insensitive scan if failed.
        if player_name in self.tracked_players:
            self.tracked_players.remove(player_name)
            if player_name in self.last_known_state:
                del self.last_known_state[player_name]
            return True
        
        # Case insensitive fallback
        target = player_name.lower()
        for p in list(self.tracked_players):
            if p.lower() == target:
                self.tracked_players.remove(p)
                if p in self.last_known_state:
                    del self.last_known_state[p]
                return True
        return False

    async def check_players(self):
        """
        Checks all tracked players and returns a list of events.
        Event format: {'type': 'join', 'player': name, 'server_info': ...}
        """
        servers_data = await self.api.fetch_servers()
        if not servers_data:
            return []

        events = []
        current_online = set()

        for player_name in self.tracked_players:
            instances = self.api.find_player(servers_data, player_name)
            
            if instances:
                # Player is online
                # For simplicity, just take the first instance found (multiboxing ignored for now)
                # Or handle multiple? Let's handle the first valid one.
                primary_instance = instances[0]
                server_info = primary_instance['server']
                
                # Construct a unique server ID (address:port)
                server_addr = f"{server_info['addresses'][0]}" 

                current_online.add(player_name)

                last_state = self.last_known_state.get(player_name)

                if not last_state:
                    # Player just came online (or bot just started)
                    # We treat this as a JOIN event
                    self.last_known_state[player_name] = {
                        'server_addr': server_addr,
                        'server_info': server_info,
                        'player_info': primary_instance['player']
                    }
                    events.append({
                        'type': 'join',
                        'player_name': player_name,
                        'server_info': server_info,
                        'player_info': primary_instance['player']
                    })
                elif last_state['server_addr'] != server_addr:
                    # Player switched servers
                    self.last_known_state[player_name] = {
                        'server_addr': server_addr,
                        'server_info': server_info,
                        'player_info': primary_instance['player']
                    }
                    events.append({
                        'type': 'switch',
                        'player_name': player_name,
                        'server_info': server_info,
                        'player_info': primary_instance['player']
                    })
                else:
                    # Player is still on the same server, update info just in case (score etc)
                     self.last_known_state[player_name]['player_info'] = primary_instance['player']
            else:
                # Player is offline
                if player_name in self.last_known_state:
                    # Player went offline
                    del self.last_known_state[player_name]
                    events.append({
                        'type': 'leave',
                        'player_name': player_name
                    })

        return events
