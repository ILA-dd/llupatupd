import aiohttp
import io
from PIL import Image, ImageDraw, ImageFont
import urllib.parse

SKIN_BASE_URL = "https://ddnet.org/skins/skin/"
MAP_BASE_URL = "https://ddnet.org/ranks/maps/"

async def fetch_image(session, url):
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        print(f"Error fetching image {url}: {e}")
    return None

async def create_composite_image(session, map_name, skin_names, player_names):
    """
    Downloads map and skins, composites them, and returns BytesIO.
    
    Args:
        session: aiohttp session
        map_name: name of the map
        skin_names: list of skin names (or single string for backwards compatibility)
        player_names: list of player names (or single string for backwards compatibility)
    """
    # Handle backwards compatibility - convert single values to lists
    if isinstance(skin_names, str):
        skin_names = [skin_names]
    if isinstance(player_names, str):
        player_names = [player_names]
    
    # Ensure all names are strings
    player_names = [str(name) for name in player_names]
    skin_names = [str(name) for name in skin_names]
    
    # Limit to 2 players max for display
    skin_names = skin_names[:2]
    player_names = player_names[:2]
    # 1. Fetch Map
    safe_map_name = urllib.parse.quote(map_name.replace(' ', '_'))
    map_url = f"{MAP_BASE_URL}{safe_map_name}.png"
    map_img = await fetch_image(session, map_url)
    
    if not map_img:
        # Create placeholder if map not found
        map_img = Image.new('RGBA', (360, 200), (40, 40, 50, 255))
        d = ImageDraw.Draw(map_img)
        try:
            font = ImageFont.truetype("arial.ttf", 26)
        except:
            try:
                font = ImageFont.truetype("Arial.ttf", 26)
            except:
                font = ImageFont.load_default()
        text = "Unknown map"
        # Position near the top (y=30) with good readability
        d.text((180, 30), text, font=font, fill=(220, 220, 220), anchor="mm")
    
    # Resize map to standard size
    map_img = map_img.resize((720, 400), resample=Image.Resampling.LANCZOS)

    # 2. Fetch and assemble Tees
    tee_renders = []
    
    for i, (skin_name, player_name) in enumerate(zip(skin_names, player_names)):
        print(f"Processing player {i}: name={player_name} (type: {type(player_name)}), skin={skin_name}")
        
        # Try to fetch skin
        skin_urls = [
            f"{SKIN_BASE_URL}{skin_name}.png",
            f"https://ddnet.org/skins/skin/community/{skin_name}.png",
            f"{SKIN_BASE_URL}default.png"
        ]
        
        skin_img = None
        for url in skin_urls:
            skin_img = await fetch_image(session, url)
            if skin_img:
                break
        
        if not skin_img:
            # Create fallback
            skin_img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(skin_img)
            draw.ellipse([64, 64, 192, 192], fill=(200, 150, 100, 255))

        # 3. Assemble this Tee
        tee_render = await assemble_tee(skin_img)
        if tee_render:
            tee_renders.append((tee_render, player_name))
    
    # 4. Composite everything
    final_img = map_img.copy()
    
    if len(tee_renders) == 1:
        # Single Tee - centered
        tee_render, player_name = tee_renders[0]
        tee_x = (final_img.width - tee_render.width) // 2
        tee_y = final_img.height - tee_render.height - 20
        
        final_img.paste(tee_render, (tee_x, tee_y), tee_render)
        draw_player_name(final_img, player_name, tee_x + tee_render.width // 2, tee_y - 15)
        
    elif len(tee_renders) == 2:
        # Two Tees - side by side
        spacing = 40  # Space between Tees
        total_width = tee_renders[0][0].width + spacing + tee_renders[1][0].width
        start_x = (final_img.width - total_width) // 2
        
        for i, (tee_render, player_name) in enumerate(tee_renders):
            if i == 0:
                # Left Tee
                tee_x = start_x
            else:
                # Right Tee
                tee_x = start_x + tee_renders[0][0].width + spacing
            
            tee_y = final_img.height - tee_render.height - 20
            final_img.paste(tee_render, (tee_x, tee_y), tee_render)
            
            # Draw name above each Tee
            name_x = tee_x + tee_render.width // 2
            name_y = tee_y - 15
            draw_player_name(final_img, player_name, name_x, name_y)
    
    # Return as BytesIO
    output = io.BytesIO()
    final_img.save(output, format="PNG")
    output.seek(0)
    return output

async def assemble_tee(skin_img):
    """Assemble a Tee from skin texture"""
    try:
        # Determine scale
        base_w = 256
        scale = skin_img.width / base_w
        
        def get_part(x, y, w, h):
            return skin_img.crop((
                int(x * scale), 
                int(y * scale), 
                int((x + w) * scale), 
                int((y + h) * scale)
            ))
        
        # Extract parts
        body = get_part(0, 0, 96, 96)
        foot_left = get_part(192, 32, 32, 32)
        foot_right = get_part(224, 32, 32, 32)
        eye_left = get_part(64, 96, 32, 32)
        eye_right = get_part(96, 96, 32, 32)
        
        # Create assembly canvas
        canvas_size = 300
        tee_canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        
        # Target sizes
        body_size = 128
        foot_size = 42
        eye_size = 42
        
        # Resize parts with NEAREST for pixel art
        body_scaled = body.resize((body_size, body_size), Image.Resampling.NEAREST)
        foot_l_scaled = foot_left.resize((foot_size, foot_size), Image.Resampling.NEAREST)
        foot_r_scaled = foot_right.resize((foot_size, foot_size), Image.Resampling.NEAREST)
        eye_l_scaled = eye_left.resize((eye_size, eye_size), Image.Resampling.NEAREST)
        eye_r_scaled = eye_right.resize((eye_size, eye_size), Image.Resampling.NEAREST)
        
        # Position elements
        center_x = canvas_size // 2
        center_y = canvas_size // 2
        
        body_x = center_x - body_size // 2
        body_y = center_y - body_size // 2
        
        # Draw feet first (behind body) - attach them higher on the body
        feet_y = body_y + int(body_size * 0.60)  # Higher position, attached to body
        feet_offset = int(body_size * 0.18)  # Closer together
        
        # Position feet to attach to body sides
        tee_canvas.paste(
            foot_l_scaled, 
            (body_x + int(body_size * 0.20) - feet_offset, feet_y),
            foot_l_scaled
        )
        tee_canvas.paste(
            foot_r_scaled, 
            (body_x + int(body_size * 0.80) - foot_size + feet_offset, feet_y),
            foot_r_scaled
        )
        
        # Draw body
        tee_canvas.paste(body_scaled, (body_x, body_y), body_scaled)
        
        # Draw eyes
        eye_y = body_y + int(body_size * 0.38)
        eye_l_x = body_x + int(body_size * 0.22)
        eye_r_x = body_x + int(body_size * 0.55)
        
        tee_canvas.paste(eye_l_scaled, (eye_l_x, eye_y), eye_l_scaled)
        tee_canvas.paste(eye_r_scaled, (eye_r_x, eye_y), eye_r_scaled)
        
        # Resize final Tee with LANCZOS for smoothing
        tee_render = tee_canvas.resize((180, 180), resample=Image.Resampling.LANCZOS)
        return tee_render
        
    except Exception as e:
        print(f"Error assembling tee: {e}")
        import traceback
        traceback.print_exc()
        return None

def draw_player_name(img, player_name, x, y):
    """Draw player name with outline at specified position"""
    # Ensure player_name is a string
    player_name = str(player_name)
    
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except:
        try:
            font = ImageFont.truetype("Arial.ttf", 32)
        except:
            font = ImageFont.load_default()
    
    # Draw outline
    outline_width = 2
    for offset_x in range(-outline_width, outline_width + 1):
        for offset_y in range(-outline_width, outline_width + 1):
            if offset_x != 0 or offset_y != 0:
                draw.text(
                    (x + offset_x, y + offset_y), 
                    player_name, 
                    font=font, 
                    fill=(0, 0, 0, 255), 
                    anchor="ms"
                )
    
    # Draw main text
    draw.text(
        (x, y), 
        player_name, 
        font=font, 
        fill=(255, 255, 255, 255), 
        anchor="ms"
    )