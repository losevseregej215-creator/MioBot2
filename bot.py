import os
import threading
import asyncio
import traceback
import requests
import discord
from discord.ext import tasks
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()


def log(*args, **kwargs):
    print(*args, **kwargs, flush=True)


DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID_STR  = os.getenv('GUILD_ID')
API_SECRET    = os.getenv('API_SECRET', 'mio_secret_default')
SITE_URL      = os.getenv('SITE_URL', 'https://miovidni.online')
PORT          = int(os.getenv('PORT', 3000))

if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN не задан")
if not GUILD_ID_STR:
    raise SystemExit("GUILD_ID не задан")

try:
    GUILD_ID = int(GUILD_ID_STR)
except ValueError:
    raise SystemExit(f"GUILD_ID должен быть числом: {GUILD_ID_STR!r}")

log(f"OK Конфиг: GUILD_ID={GUILD_ID}, PORT={PORT}, SITE={SITE_URL}")
log(f"OK API_SECRET: {'задан' if API_SECRET else 'НЕ ЗАДАН'}")
log(f"OK discord.py версия: {discord.__version__}")

intents = discord.Intents.default()
intents.presences = True
intents.members   = True
intents.guilds    = True


def get_presence_data(member):
    if not member or not member.status:
        return {'online': False, 'status': 'offline', 'in_guild': True,
                'activity': None, 'spotify': None,
                'username': None, 'avatar': None}

    status = str(member.status)

    # Аватарка и ник
    try:
        avatar_url = str(member.display_avatar.url) if member.display_avatar else None
    except Exception:
        avatar_url = None

    username = member.display_name or member.name or None

    data = {'online': status != 'offline', 'status': status, 'in_guild': True,
            'activity': None, 'spotify': None,
            'username': username, 'avatar': avatar_url}

    activity = None
    spotify  = None

    if member.activities:
        for act in member.activities:
            try:
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
            except Exception as e:
                log(f'WARN Ошибка разбора activity: {e}')

    data['activity'] = activity
    data['spotify']  = spotify
    return data


def send_to_site(payload):
    """Отправка на сайт с правильными заголовками, чтобы Cloudflare не блочил."""
    try:
        r = requests.post(
            f'{SITE_URL}/api/save_presence.php',
            headers={
                'X-API-Secret': API_SECRET,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            },
            json=payload,
            timeout=15
        )
        return r.status_code, r.text[:300]
    except Exception as e:
        return 0, str(e)


class MioBot(discord.Client):
    async def setup_hook(self):
        log('START setup_hook: запускаю цикл отправки')
        push_presence_to_site.start()
        log('OK Цикл отправки запущен')


bot = MioBot(intents=intents)
app = Flask(__name__)


@tasks.loop(seconds=30)
async def push_presence_to_site():
    log('CYCLE Цикл отправки сработал')
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            log('WARN Сервер не найден')
            return

        users = []
        for member in guild.members:
            if member.bot:
                continue
            data = get_presence_data(member)
            users.append({'discord_id': str(member.id), **data})

        log(f'INFO Участников (не ботов): {len(users)}')

        if not users:
            log('WARN Нет участников для отправки')
            return

        payload = {'users': users, 'secret': API_SECRET}

        loop = asyncio.get_event_loop()
        code, text = await loop.run_in_executor(None, send_to_site, payload)

        log(f'SEND Отправлено {len(users)} статусов -> HTTP {code}')
        if code != 200:
            log(f'WARN Ответ сервера: {text}')

    except Exception as e:
        log(f'ERROR Ошибка в цикле: {e}')
        traceback.print_exc()


@bot.event
async def on_ready():
    log(f'READY on_ready: бот {bot.user} (ID: {bot.user.id})')
    guild = bot.get_guild(GUILD_ID)
    if guild:
        log(f'GUILD Сервер: {guild.name} - {guild.member_count} участников')
    else:
        log(f'WARN Бот не в сервере {GUILD_ID}!')


@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'botReady': bot.is_ready(),
        'guild': str(GUILD_ID),
        'loopRunning': push_presence_to_site.is_running()
    })


@app.route('/test_push')
def test_push():
    try:
        r = requests.post(
            f'{SITE_URL}/api/save_presence.php',
            headers={
                'X-API-Secret': API_SECRET,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            },
            json={'users': [{'discord_id': '0', 'status': 'test'}], 'secret': API_SECRET},
            timeout=10
        )
        return jsonify({'status_code': r.status_code, 'response': r.text[:300]})
    except Exception as e:
        return jsonify({'error': str(e)})


def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    log(f'FLASK Flask запущен на порту {PORT}')
    log('BOT Запускаю Discord-бота...')
    bot.run(DISCORD_TOKEN)
