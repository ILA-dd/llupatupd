import aiohttp
import io
from PIL import Image, ImageDraw, ImageFont
import urllib.parse
import os

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

def load_local_image(filename):
    """Load image from local file"""
    try:
        if os.path.exists(filename):
            return Image.open(filename).convert("RGBA")
    except Exception as e:
        print(f"Error loading local image {filename}: {e}")
    return None

def get_font(size=42):
    """Get font with fallback"""
    try:
        return ImageFont.truetype("arial.ttf", size)
    except:
        try:
            return ImageFont.truetype("Arial.ttf", size)
        except:
            return ImageFont.load_default()

async def create_composite_image(session, map_name, skin_names, player_names, server_name=""):
    """
    Downloads map and skins, composites them, and returns BytesIO.
    """
    # Normalize inputs
    if isinstance(skin_names, str):
        skin_names = [skin_names]
    if isinstance(player_names, str):
        player_names = [player_names]
    
    player_names = [str(name) for name in player_names]
    skin_names = [str(name) for name in skin_names]
    
    # Limit to 2 players
    skin_names = skin_names[:2]
    player_names = player_names[:2]
    
    # Load background
    map_img = await load_background(session, server_name, map_name)
    map_img = map_img.resize((720, 400), resample=Image.Resampling.LANCZOS)
    
    # Fetch and render tees
    tee_data = []
    for skin_name, player_name in zip(skin_names, player_names):
        skin_img = await fetch_skin(session, skin_name)
        tee_render = await assemble_tee(skin_img)
        if tee_render:
            tee_data.append({
                'image': tee_render,
                'name': player_name,
                'width': tee_render.width,
                'height': tee_render.height
            })
    
    # Composite on map
    final_img = composite_tees_on_map(map_img, tee_data)
    
    # Convert to BytesIO
    output = io.BytesIO()
    final_img.save(output, format="PNG")
    output.seek(0)
    return output

async def load_background(session, server_name, map_name):
    """Load custom or default background"""
    print(f"[DEBUG] Server: '{server_name}', Map: '{map_name}'")
    
    # Check for TeeFusion custom backgrounds
    if "TeeFusion" in server_name:
        print(f"[DEBUG] TeeFusion server detected")
        if "Block" in server_name or "Copy Box" in server_name:
            bg = load_local_image("TF.png")
            if bg:
                print(f"✅ Loaded TF.png")
                return bg
        elif "FNG" in server_name:
            bg = load_local_image("FNG TF.png")
            if bg:
                print(f"✅ Loaded FNG TF.png")
                return bg
    
    # Fetch from DDNet
    safe_map_name = urllib.parse.quote(map_name.replace(' ', '_'))
    map_url = f"{MAP_BASE_URL}{safe_map_name}.png"
    map_img = await fetch_image(session, map_url)
    
    if map_img:
        return map_img
    
    # Create placeholder
    placeholder = Image.new('RGBA', (360, 200), (40, 40, 50, 255))
    draw = ImageDraw.Draw(placeholder)
    font = get_font(26)
    draw.text((180, 30), "Unknown map", font=font, fill=(220, 220, 220), anchor="mm")
    return placeholder

async def fetch_skin(session, skin_name):
    """Fetch skin with fallback"""
    skin_urls = [
        f"{SKIN_BASE_URL}{skin_name}.png",
        f"https://ddnet.org/skins/skin/community/{skin_name}.png",
        f"{SKIN_BASE_URL}default.png"
    ]
    
    for url in skin_urls:
        skin_img = await fetch_image(session, url)
        if skin_img:
            return skin_img
    
    # Fallback skin
    fallback = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(fallback)
    draw.ellipse([64, 64, 192, 192], fill=(200, 150, 100, 255))
    return fallback

async def assemble_tee(skin_img):
    """Assemble a Tee from skin texture"""
    try:
        # Calculate scale
        base_w = 256
        scale = skin_img.width / base_w
        
        def crop_part(x, y, w, h):
            return skin_img.crop((
                int(x * scale), int(y * scale),
                int((x + w) * scale), int((y + h) * scale)
            ))
        
        # Extract parts
        body = crop_part(0, 0, 96, 96)
        foot_left = crop_part(192, 32, 32, 32)
        foot_right = crop_part(224, 32, 32, 32)
        eye_left = crop_part(64, 96, 32, 32)
        eye_right = crop_part(96, 96, 32, 32)
        
        # Canvas setup
        canvas_size = 300
        tee_canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        
        # Sizes
        body_size = 128
        foot_size = 42
        eye_size = 42
        
        # Scale parts
        body_scaled = body.resize((body_size, body_size), Image.Resampling.NEAREST)
        foot_l = foot_left.resize((foot_size, foot_size), Image.Resampling.NEAREST)
        foot_r = foot_right.resize((foot_size, foot_size), Image.Resampling.NEAREST)
        eye_l = eye_left.resize((eye_size, eye_size), Image.Resampling.NEAREST)
        eye_r = eye_right.resize((eye_size, eye_size), Image.Resampling.NEAREST)
        
        # Calculate positions
        center = canvas_size // 2
        body_x = center - body_size // 2
        body_y = center - body_size // 2
        
        # Place feet
        feet_y = body_y + int(body_size * 0.60)
        feet_offset = int(body_size * 0.18)
        
        tee_canvas.paste(foot_l, (body_x + int(body_size * 0.20) - feet_offset, feet_y), foot_l)
        tee_canvas.paste(foot_r, (body_x + int(body_size * 0.80) - foot_size + feet_offset, feet_y), foot_r)
        
        # Place body
        tee_canvas.paste(body_scaled, (body_x, body_y), body_scaled)
        
        # Place eyes
        eye_y = body_y + int(body_size * 0.38)
        tee_canvas.paste(eye_l, (body_x + int(body_size * 0.22), eye_y), eye_l)
        tee_canvas.paste(eye_r, (body_x + int(body_size * 0.55), eye_y), eye_r)
        
        # Final resize
        return tee_canvas.resize((240, 240), resample=Image.Resampling.LANCZOS)
        
    except Exception as e:
        print(f"Error assembling tee: {e}")
        return None

def composite_tees_on_map(map_img, tee_data):
    """Composite tees with names onto map"""
    result = map_img.copy()
    draw = ImageDraw.Draw(result)
    font = get_font(42)
    
    num_tees = len(tee_data)
    
    if num_tees == 0:
        return result
    
    # Bottom padding for tees
    bottom_padding = 20
    
    if num_tees == 1:
        # Center single tee
        tee = tee_data[0]
        tee_x = (result.width - tee['width']) // 2
        tee_y = result.height - tee['height'] - bottom_padding
        
        # Calculate name position - above the tee's head area
        # Head is approximately at 30% from top of tee image
        head_offset = int(tee['height'] * 0.23)
        name_x = tee_x + tee['width'] // 2
        name_y = tee_y + head_offset
        
        # Draw name
        draw.text((name_x, name_y), tee['name'], font=font, fill=(255, 255, 255), anchor="mb")
        
        # Draw tee
        result.paste(tee['image'], (tee_x, tee_y), tee['image'])
        
    elif num_tees == 2:
        # Side by side
        spacing = 40
        total_width = tee_data[0]['width'] + spacing + tee_data[1]['width']
        start_x = (result.width - total_width) // 2
        
        for i, tee in enumerate(tee_data):
            if i == 0:
                tee_x = start_x
            else:
                tee_x = start_x + tee_data[0]['width'] + spacing
            
            tee_y = result.height - tee['height'] - bottom_padding
            
            # Calculate name position
            head_offset = int(tee['height'] * 0.23)
            name_x = tee_x + tee['width'] // 2
            name_y = tee_y + head_offset
            
            # Draw name
            draw.text((name_x, name_y), tee['name'], font=font, fill=(255, 255, 255), anchor="mb")
            
            # Draw tee
            result.paste(tee['image'], (tee_x, tee_y), tee['image'])
    
    return result