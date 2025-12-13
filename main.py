import discord
from discord.ext import commands, tasks
import json
import logging
import asyncio
from api import DDNetAPI
from tracker import PlayerTracker
import image_utils
import aiohttp

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('bot')

# Load Config
with open('config.json', 'r') as f:
    config = json.load(f)

# Set default check interval if not in config
if 'check_interval' not in config:
    config['check_interval'] = 15
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

# Initialize Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize API and Tracker
api = DDNetAPI()
tracker = PlayerTracker(api)

# Pre-load tracked players
for p in config.get('tracked_players', []):
    tracker.add_player(p)

def save_config():
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

def get_server_key(addr_list):
    if not addr_list: return "unknown"
    # Sort addresses to ensure consistency regardless of order from API
    sorted_addrs = sorted(addr_list)
    # Use the first one as the key
    return sorted_addrs[0]

def is_authorized():
    async def predicate(ctx):
        if ctx.author.id not in config.get('authorized_users', []):
            await ctx.send("⛔ У тебя нет доступа к этой команде.")
            return False
        return True
    return commands.check(predicate)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    check_players_loop.change_interval(seconds=config.get('check_interval', 15))
    check_players_loop.start()

@tasks.loop(seconds=15)
async def check_players_loop():
    try:
        # First, get current state of all tracked players
        servers_data = await api.fetch_servers()
        if not servers_data:
            return
            
        # Find all tracked players and their current servers
        current_state = {}  # {server_addr: [player_names]}
        for player_name in tracker.tracked_players:
            instances = api.find_player(servers_data, player_name)
            if instances:
                server_addr = instances[0]['server']['addresses'][0]
                if server_addr not in current_state:
                    current_state[server_addr] = []
                current_state[server_addr].append({
                    'name': player_name,
                    'info': instances[0]['player'],
                    'server': instances[0]['server']
                })
        
        # Get channel
        channel_id = config.get('notification_channel_id')
        channel = None
        if channel_id:
            channel = bot.get_channel(channel_id)
        
        if not channel:
            for guild in bot.guilds:
                if guild.system_channel:
                    channel = guild.system_channel
                    break
                for c in guild.text_channels:
                    if c.permissions_for(guild.me).send_messages:
                        channel = c
                        break
                if channel: break
        
        if not channel:
            return
            
        # Startup cleanup - Run only ONCE
        if not hasattr(bot, 'startup_cleanup_done'):
            bot.startup_cleanup_done = True
            try:
                # Delete recent messages from the bot in this channel
                async for msg in channel.history(limit=50):
                    if msg.author == bot.user:
                        await msg.delete()
            except Exception as e:
                logger.error(f"Error during startup cleanup: {e}")

        # Compare with previous state
        if not hasattr(bot, 'previous_server_state'):
            bot.previous_server_state = {}
            
        # Refine current_state to use stable keys
        stable_current_state = {}
        for addr, players in current_state.items():
            if players:
                all_addrs = players[0]['server'].get('addresses', [])
                stable_key = get_server_key(all_addrs)
                stable_current_state[stable_key] = players

        # Check for new servers or changed player lists
        for server_addr, players in stable_current_state.items():
            player_names = [p['name'] for p in players]
            prev_players = bot.previous_server_state.get(server_addr, [])
            
            # If server is new or player list changed, send/update notification
            if set(player_names) != set(prev_players):
                await send_grouped_notification(channel, players, server_addr)
        
        # Check for servers that players left
        for server_addr in list(bot.previous_server_state.keys()):
            if server_addr not in stable_current_state:
                # All players left this server
                await remove_server_notification(channel, server_addr)
        
        # Update state with STABLE keys
        bot.previous_server_state = {
            addr: [p['name'] for p in players] 
            for addr, players in stable_current_state.items()
        }
                    
    except Exception as e:
        logger.error(f"Error in check_players_loop: {e}")
        import traceback
        traceback.print_exc()

# Store active notification messages: {server_addr: message_id}
active_messages = {}

async def send_grouped_notification(channel, players, server_addr):
    """Send a single notification for multiple players on the same server, editing if possible."""
    if not players:
        return
    
    # 1. Prepare Embed and File (common logic)
    # Use first player's server info (all on same server)
    s_info = players[0]['server']
    
    # Parse server details
    server_name = s_info.get('info', {}).get('name', 'Unknown Server')
    map_name = s_info.get('info', {}).get('map', {}).get('name', 'Unknown Map')
    country = s_info.get('location', 'UNK')
    
    # Process address
    import re
    raw_addr = s_info.get('addresses', [''])[0]
    ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', raw_addr)
    if ip_match:
        clean_addr = ip_match.group(1)
    else:
        clean_addr = raw_addr

    def get_flag(loc):
        if not loc: return "🏳️"
        try:
            if ':' in loc:
                code = loc.split(':')[-1]
            else:
                code = loc
            
            if len(code) == 2:
                code = code.upper()
                return chr(ord(code[0]) + 127397) + chr(ord(code[1]) + 127397)
            return "🏳️"
        except:
            return "🏳️"

    flag = get_flag(country)
    
    # Build description with all players
    player_names = [p['name'] for p in players]
    if len(player_names) == 1:
        description = f"**{player_names[0]}** is playing\n{flag} **{map_name}**"
    elif len(player_names) == 2:
        description = f"**{player_names[0]}** and **{player_names[1]}** are playing\n{flag} **{map_name}**"
    else:
        # 3 or more players: "nick1, nick2, nick3 and nick4 are playing"
        formatted_names = [f"**{name}**" for name in player_names[:-1]]
        players_str = ", ".join(formatted_names) + f" and **{player_names[-1]}**"
        description = f"{players_str} are playing\n{flag} **{map_name}**"

    embed = discord.Embed(
        title=f"{server_name}",
        description=description,
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    
    # Generate composite image
    if not api.session:
        await api.init_session()
    
    # Collect skins and names for all players (max 2)
    skin_names = []
    display_names = []
    
    for player in players[:2]:  # Limit to 2 players
        skin_name = player['info'].get('skin', {}).get('name', 'default')
        player_name = player['name']
        print(f"Preparing player: name={player_name} (type: {type(player_name)}), skin={skin_name}")
        skin_names.append(skin_name)
        display_names.append(player_name)
    
    img_buffer = await image_utils.create_composite_image(
        api.session, 
        map_name, 
        skin_names, 
        display_names
    )
    file = discord.File(fp=img_buffer, filename="notification.png")
    
    embed.set_image(url="attachment://notification.png")
    embed.set_thumbnail(url="https://ddnet.org/static/ddnet-white.png") 
    
    current_players = len(s_info.get('info', {}).get('clients', []))
    max_players = s_info.get('info', {}).get('max_clients', 64)
    
    embed.add_field(name="Current Players", value=f"{current_players}/{max_players}", inline=True)
    
    # Show time for first player
    first_player_info = players[0]['info']
    if 'score' in first_player_info:
        score = first_player_info['score']
        if score != 0 and score != -9999:
            time_seconds = abs(score)
            hours = time_seconds // 3600
            minutes = (time_seconds % 3600) // 60
            seconds = time_seconds % 60
            
            if hours > 0:
                time_str = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                time_str = f"{minutes}:{seconds:02d}"
            
            embed.add_field(name="Time", value=f"`{time_str}`", inline=True)
        else:
            embed.add_field(name="Time", value="Unfinished", inline=True)

    embed.add_field(name="Server IP", value=f"`{clean_addr}`", inline=False)
    embed.set_footer(text=f"Today at {discord.utils.utcnow().strftime('%I:%M %p')}")
    
    http_connect_url = f"https://ddnet.org/connect-to/?addr={clean_addr}"
    
    view = discord.ui.View()
    button = discord.ui.Button(label="Connect", style=discord.ButtonStyle.link, url=http_connect_url)
    view.add_item(button)

    # 2. Try to Edit existing message, else Send new
    msg_id = active_messages.get(server_addr)
    sent = False
    
    if msg_id:
        try:
            old_msg = await channel.fetch_message(msg_id)
            if old_msg:
                # Update the message
                await old_msg.edit(embed=embed, view=view, attachments=[file])
                sent = True
        except discord.NotFound:
            # Message was deleted manually? Clean up
            pass
        except Exception as e:
            logger.error(f"Failed to edit message for {server_addr}: {e}")
    
    if not sent:
        # Send new message
        try:
            msg = await channel.send(embed=embed, view=view, file=file)
            active_messages[server_addr] = msg.id
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

async def remove_server_notification(channel, server_addr):
    """Remove notification when all players leave a server"""
    if server_addr in active_messages:
        try:
            msg = await channel.fetch_message(active_messages[server_addr])
            if msg:
                await msg.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
        
        del active_messages[server_addr]

@bot.command()
@is_authorized()
async def track(ctx, action: str = None, *, player_name: str = None):
    """
    Manage tracked players.
    Usage:
    !track add <player_name>
    !track remove <player_name>
    !track list
    !track interval <seconds>
    """
    if action == "add" and player_name:
        if player_name.lower() not in [p.lower() for p in config['tracked_players']]:
            config['tracked_players'].append(player_name)
            save_config()
            tracker.add_player(player_name)
            await ctx.send(f"Now tracking **{player_name}**.")
            
            if 'notification_channel_id' not in config:
                config['notification_channel_id'] = ctx.channel.id
                save_config()
                await ctx.send(f"Set this channel as the notification channel.")
        else:
            await ctx.send(f"Already tracking **{player_name}**.")
            
    elif action == "remove" and player_name:
        original_list = config['tracked_players']
        new_list = [p for p in original_list if p.lower() != player_name.lower()]
        
        if len(new_list) < len(original_list):
            config['tracked_players'] = new_list
            save_config()
            tracker.remove_player(player_name)
            await ctx.send(f"Stopped tracking **{player_name}**.")
        else:
             await ctx.send(f"Not tracking **{player_name}**.")

    elif action == "list":
        if config['tracked_players']:
            msg = "**Tracked Players:**\n" + "\n".join(config['tracked_players'])
        else:
            msg = "No players are being tracked."
        await ctx.send(msg)
    
    elif action == "interval":
        if player_name:
            try:
                # player_name contains the seconds value in this case
                seconds = int(player_name)
                if seconds < 5:
                    await ctx.send("⚠️ Минимальный интервал: 5 секунд.")
                    return
                if seconds > 300:
                    await ctx.send("⚠️ Максимальный интервал: 300 секунд (5 минут).")
                    return
                
                # Save to config
                config['check_interval'] = seconds
                save_config()
                
                # Restart loop with new interval
                check_players_loop.change_interval(seconds=seconds)
                if check_players_loop.is_running():
                    check_players_loop.restart()
                else:
                    check_players_loop.start()
                
                await ctx.send(f"✅ Интервал проверки установлен на **{seconds}** секунд.")
            except ValueError:
                await ctx.send("❌ Укажите корректное число секунд.")
        else:
            await ctx.send("Использование: `!track interval <секунды>`")
        
    else:
        await ctx.send("Использование: `!track add <player>`, `!track remove <player>`, `!track list`, `!track interval <секунды>`")

@bot.command()
async def tracker_status(ctx):
    """Shows current tracker status and online players."""
    # Determine overall status
    loop_running = check_players_loop.is_running()
    api_working = False
    overall_status = "🔴 Not Working"
    status_color = discord.Color.red()
    
    # Test API connection
    try:
        test_data = await api.fetch_servers()
        if test_data and 'servers' in test_data:
            api_working = True
    except:
        api_working = False
    
    # Determine overall status
    if loop_running and api_working:
        overall_status = "🟢 Working"
        status_color = discord.Color.green()
    elif loop_running and not api_working:
        overall_status = "🟡 Partial (API Error)"
        status_color = discord.Color.orange()
    elif not loop_running:
        overall_status = "🔴 Not Working (Loop Stopped)"
        status_color = discord.Color.red()
    
    embed = discord.Embed(
        title="📊 Tracker Status",
        description=f"**Status:** {overall_status}",
        color=status_color,
        timestamp=discord.utils.utcnow()
    )
    
    # Loop status
    loop_status = "🟢 Running" if loop_running else "🔴 Stopped"
    embed.add_field(name="Tracking Loop", value=loop_status, inline=True)
    
    # API status
    api_status = "🟢 Connected" if api_working else "🔴 Connection Failed"
    embed.add_field(name="DDNet API", value=api_status, inline=True)
    
    # Tracked players count
    tracked_count = len(config.get('tracked_players', []))
    embed.add_field(name="Tracked Players", value=str(tracked_count), inline=True)
    
    # Notification channel
    channel_id = config.get('notification_channel_id')
    if channel_id:
        channel = bot.get_channel(channel_id)
        channel_mention = channel.mention if channel else f"⚠️ Channel not found (ID: {channel_id})"
    else:
        channel_mention = "⚠️ Not set"
    embed.add_field(name="Notification Channel", value=channel_mention, inline=False)
    
    # Check online players
    if api_working:
        try:
            servers_data = await api.fetch_servers()
            if servers_data:
                online_players = []
                for player_name in tracker.tracked_players:
                    instances = api.find_player(servers_data, player_name)
                    if instances:
                        server_info = instances[0]['server']
                        map_name = server_info.get('info', {}).get('map', {}).get('name', 'Unknown')
                        online_players.append(f"🟢 **{player_name}** - {map_name}")
                
                if online_players:
                    embed.add_field(
                        name=f"Online Now ({len(online_players)})",
                        value="\n".join(online_players),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Online Now",
                        value="No tracked players online",
                        inline=False
                    )
        except Exception as e:
            logger.error(f"Error checking online players: {e}")
            embed.add_field(
                name="Online Status",
                value="⚠️ Error checking online status",
                inline=False
            )
    else:
        embed.add_field(
            name="Online Status",
            value="⚠️ Cannot check (API not available)",
            inline=False
        )
    
    # Active notifications
    active_count = len(active_messages)
    embed.add_field(name="Active Notifications", value=str(active_count), inline=True)
    
    # Admins count
    admin_count = len(config.get('authorized_users', []))
    embed.add_field(name="Authorized Admins", value=str(admin_count), inline=True)
    
    # Check interval
    check_interval = config.get('check_interval', 15)
    embed.add_field(name="Check Interval", value=f"{check_interval} seconds", inline=True)
    
    embed.set_footer(text="Создано кланом llUPAT | Разработчик: ILA")
    
    await ctx.send(embed=embed)

@bot.command()
@is_authorized()
async def setchannel(ctx):
    """Sets the current channel for notifications."""
    old_channel_id = config.get('notification_channel_id')
    new_channel_id = ctx.channel.id
    
    # Set new channel first
    config['notification_channel_id'] = new_channel_id
    save_config()
    
    # If changing to a different channel, clean up old messages silently
    if old_channel_id and old_channel_id != new_channel_id:
        old_channel = bot.get_channel(old_channel_id)
        if old_channel:
            # Delete all active messages from old channel in background
            for msg_id in list(active_messages.values()):
                try:
                    msg = await old_channel.fetch_message(msg_id)
                    if msg:
                        await msg.delete()
                except:
                    pass  # Ignore any errors
            
            # Clear active messages dict
            active_messages.clear()
    
    # Send confirmation message
    await ctx.send(f"Notifications will now appear in {ctx.channel.mention}")
    
    # Reset previous state to force recreation of notifications
    if hasattr(bot, 'previous_server_state'):
        bot.previous_server_state = {}
    
    # Wait a moment then trigger check
    await asyncio.sleep(1)
    
    # Manually trigger a check to populate new channel immediately
    try:
        servers_data = await api.fetch_servers()
        if not servers_data:
            return
            
        # Find all tracked players and their current servers
        current_state = {}
        for player_name in tracker.tracked_players:
            instances = api.find_player(servers_data, player_name)
            if instances:
                server_addr = instances[0]['server']['addresses'][0]
                if server_addr not in current_state:
                    current_state[server_addr] = []
                current_state[server_addr].append({
                    'name': player_name,
                    'info': instances[0]['player'],
                    'server': instances[0]['server']
                })
        
        # Use simple stable key conversion to match check_players_loop logic
        # Ideally we refactor this finding logic to be shared, but for now just stabilize the key
        stable_current_state = {}
        for addr, players in current_state.items():
            if players:
                all_addrs = players[0]['server'].get('addresses', [])
                stable_key = get_server_key(all_addrs)
                stable_current_state[stable_key] = players

        # Send notifications for all active servers
        for server_addr, players in stable_current_state.items():
            await send_grouped_notification(ctx.channel, players, server_addr)
        
        # Update state
        bot.previous_server_state = {
            addr: [p['name'] for p in players] 
            for addr, players in stable_current_state.items()
        }
        
    except Exception as e:
        logger.error(f"Error in immediate check after channel change: {e}")


@bot.command()
@is_authorized()
async def track_admin(ctx, action: str = None, user_id: int = None):
    """
    Manage admin users.
    Usage:
    !track_admin add <user_id>
    """
    if action == "add" and user_id:
        if user_id not in config['authorized_users']:
            config['authorized_users'].append(user_id)
            save_config()
            await ctx.send(f"✅ User ID **{user_id}** теперь админ.")
        else:
            await ctx.send(f"⚠️ User ID **{user_id}** уже админ.")
    else:
        await ctx.send("Использование: `!track_admin add <user_id>`")


@bot.command()
async def track_help(ctx):
    """Shows help information about the bot commands."""
    embed = discord.Embed(
        title="🤖 DDNet Tracker Bot Help",
        description="Доступные команды:",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="✅ !track add <name>", value="Добавить игрока в трекер", inline=False)
    embed.add_field(name="❌ !track remove <name>", value="Удалить игрока из трекера", inline=False)
    embed.add_field(name="📜 !track list", value="Показать список игроков в трекере", inline=False)
    embed.add_field(name="📊 !tracker_status", value="Показать статус трекера и онлайн игроков", inline=False)
    embed.add_field(name="📢 !setchannel", value="Установить текущий канал для уведомлений", inline=False)
    embed.add_field(name="🎉 !track_admin add <user_id>", value="Добавить пользователя в список админов", inline=False)
    
    embed.set_footer(text="Создано кланом llUPAT | Разработчик: ILA")
    
    await ctx.send(embed=embed)

if __name__ == "__main__":
    if config['bot_token'] == "YOUR_BOT_TOKEN_HERE":
        print("Please set your 'bot_token' in config.json")
    else:
        bot.run(config['bot_token'])