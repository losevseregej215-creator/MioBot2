import os
import threading
import asyncio
import requests
import discord
from discord.ext import commands, tasks
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

# ─── Конфигурация ───
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID_STR  = os.getenv('GUILD_ID')
API_SECRET    = os.getenv('API_SECRET', 'mio_secret_default')
SITE_URL      = os.getenv('SITE_URL', 'https://miovidni.online')
PORT          = int(os.getenv('PORT', 3000))   # ← 3000 по умолчанию

if not DISCORD_TOKEN:
    raise SystemExit("❌ DISCORD_TOKEN не задан")
if not GUILD_ID_STR:
    raise SystemExit("❌ GUILD_ID не задан")

try:
    GUILD_ID = int(GUILD_ID_STR)
except ValueError:
    raise SystemExit(f"❌ GUILD_ID должен быть числом: {GUILD_ID_STR!r}")

print(f"✅ Конфиг: GUILD_ID={GUILD_ID}, PORT={PORT}, SITE={SITE_URL}")

intents = discord.Intents.default()
intents.presences = True
intents.members   = True
intents.guilds    = True

bot = discord.Client(intents=intents)
app = Flask(__name__)


def get_presence_data(user_id: int) -> dict:
    default = {
        'online':   False,
        'status':   'offline',
        'in_guild': False,
        'activity': None,
        'spotify':  None
    }
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return default

    member = guild.get_member(user_id)
    if not member:
        return default

    default['in_guild'] = True
    status = str(member.status) if member.status else 'offline'
    default['status'] = status
    default['online'] = status != 'offline'

    activity = None
    spotify  = None

    if member.activities:
        for act in member.activities:
            if act.type == discord.ActivityType.playing:
                image_url = None
                if act.assets and act.assets.large_image:
                    img = act.assets.large_image
                    if img.startswith('spotify:'):
                        image_url = f"https://i.scdn.co/image/{img.split(':')[1]}"
                    elif img.startswith('mp:'):
                        image_url = f"https://media.discordapp.net/{img[3:]}"
                    else:
                        app_id = act.application_id
                        if app_id:
                            image_url = f"https://cdn.discordapp.com/app-assets/{app_id}/{img}.png"
                activity = {
                    'type': 'playing', 'name': act.name,
                    'details': act.details, 'state': act.state,
                    'image': image_url
                }
                break
            elif isinstance(act, discord.Spotify):
                spotify = {
                    'song': act.title, 'artist': act.artist,
                    'album': act.album, 'image': act.album_cover_url
                }
                activity = {
                    'type': 'listening', 'name': 'Spotify',
                    'details': act.title, 'state': act.artist,
                    'image': act.album_cover_url
                }
            elif act.type == discord.ActivityType.watching:
                activity = {
                    'type': 'watching', 'name': act.name,
                    'details': act.details, 'state': act.state,
                    'image': None
                }
            elif act.type == discord.ActivityType.custom:
                continue

    default['activity'] = activity
    default['spotify']  = spotify
    return default


def send_to_site(payload: dict) -> tuple:
    try:
        r = requests.post(
            f'{SITE_URL}/api/save_presence.php',
            headers={
                'X-API-Secret': API_SECRET,
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=15
        )
        return r.status_code, r.text[:200]
    except Exception as e:
        return 0, str(e)


@tasks.loop(seconds=30)
async def push_presence_to_site():
    print('🔄 Цикл отправки сработал')
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print('⚠️ Сервер не найден')
        return

    users = []
    for member in guild.members:
        if member.bot:
            continue
        data = get_presence_data(member.id)
        users.append({'discord_id': str(member.id), **data})

    if not users:
        print('⚠️ Нет пользователей для отправки')
        return

    payload = {'users': users, 'secret': API_SECRET}

    loop = asyncio.get_event_loop()
    code, text = await loop.run_in_executor(None, send_to_site, payload)

    print(f'📤 Отправлено {len(users)} статусов → HTTP {code}')
    if code != 200:
        print(f'⚠️ Ответ сервера: {text}')


@app.route('/api/status/<int:user_id>', methods=['GET'])
def api_status(user_id: int):
    if request.headers.get('X-API-Secret') != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'success': True, **get_presence_data(user_id)})


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'status': 'ok',
        'botReady': bot.is_ready(),
        'guild': str(GUILD_ID)
    })


@app.route('/test_push', methods=['GET'])
def test_push():
    try:
        r = requests.post(
            f'{SITE_URL}/api/save_presence.php',
            headers={'X-API-Secret': API_SECRET, 'Content-Type': 'application/json'},
            json={'users': [{'discord_id': '0', 'status': 'test'}], 'secret': API_SECRET},
            timeout=10
        )
        return jsonify({
            'sent_secret': API_SECRET,
            'site_url': SITE_URL,
            'status_code': r.status_code,
            'response': r.text[:300]
        })
    except Exception as e:
        return jsonify({'error': str(e), 'sent_secret': API_SECRET, 'site_url': SITE_URL})


def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


@bot.event
async def on_ready():
    print(f'✅ Бот запущен как {bot.user} (ID: {bot.user.id})')
    guild = bot.get_guild(GUILD_ID)
    if guild:
        async for _ in guild.fetch_members(limit=None):
            pass
        print(f'📡 Сервер: {guild.name} — {guild.member_count} участников')
    else:
        print(f'⚠️ Бот не в сервере {GUILD_ID}!')

    if not push_presence_to_site.is_running():
        push_presence_to_site.start()
        print('🔁 Периодическая отправка запущена (каждые 30 сек)')


@bot.event
async def on_member_update(before, after):
    if before.status != after.status:
        print(f'🔄 {after.display_name}: {before.status} → {after.status}')


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
