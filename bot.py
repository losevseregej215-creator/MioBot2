import os
import json
import threading
import discord
from discord.ext import commands
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID      = int(os.getenv('GUILD_ID'))
API_SECRET    = os.getenv('API_SECRET')
PORT          = int(os.getenv('PORT', 3000))

# ─── Интенты (Presence + Members обязательны!) ───
intents = discord.Intents.default()
intents.presences = True
intents.members   = True
intents.guilds    = True

bot = discord.Client(intents=intents)

# ─── Flask-приложение для HTTP API ───
app = Flask(__name__)


def get_presence_data(user_id: int) -> dict:
    """
    Собирает данные о присутствии пользователя:
    статус, активность (игра, Spotify, просмотр).
    """
    default = {
        'online':    False,
        'status':    'offline',
        'in_guild':  False,
        'activity':  None,
        'spotify':   None
    }

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return default

    member = guild.get_member(user_id)
    if not member:
        return default

    # Пользователь на сервере, но может быть offline
    default['in_guild'] = True

    # Получаем актуальный объект member с presence
    # (у участника без presence будет member.status = offline)
    status = str(member.status) if member.status else 'offline'
    default['status'] = status
    default['online'] = status != 'offline'

    activity = None
    spotify  = None

    if member.activities:
        for act in member.activities:
            # Игра (Playing)
            if act.type == discord.ActivityType.playing:
                image_url = None
                if act.assets and act.assets.large_image:
                    img = act.assets.large_image
                    # Spotify/внешние картинки приходят как "spotify:xxx"
                    if img.startswith('spotify:'):
                        image_url = f"https://i.scdn.co/image/{img.split(':')[1]}"
                    elif img.startswith('mp:'):
                        image_url = f"https://media.discordapp.net/{img[3:]}"
                    else:
                        app_id = act.application_id
                        if app_id:
                            image_url = f"https://cdn.discordapp.com/app-assets/{app_id}/{img}.png"

                activity = {
                    'type':    'playing',
                    'name':    act.name,
                    'details': act.details,
                    'state':   act.state,
                    'image':   image_url
                }
                break  # берём первую игру, остальные пропускаем

            # Spotify (Listening)
            elif isinstance(act, discord.Spotify):
                spotify = {
                    'song':   act.title,
                    'artist': act.artist,
                    'album':  act.album,
                    'image':  act.album_cover_url
                }
                activity = {
                    'type':    'listening',
                    'name':    'Spotify',
                    'details': act.title,
                    'state':   act.artist,
                    'image':   act.album_cover_url
                }
                # Spotify не break'аем — вдруг ещё есть игра

            # Watching
            elif act.type == discord.ActivityType.watching:
                activity = {
                    'type':    'watching',
                    'name':    act.name,
                    'details': act.details,
                    'state':   act.state,
                    'image':   None
                }

            # Custom status — пропускаем
            elif act.type == discord.ActivityType.custom:
                continue

    default['activity'] = activity
    default['spotify']  = spotify
    return default


# ─── HTTP API ───
@app.route('/api/status/<int:user_id>', methods=['GET'])
def api_status(user_id: int):
    # Проверка секретного ключа
    if request.headers.get('X-API-Secret') != API_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    data = get_presence_data(user_id)
    return jsonify({'success': True, **data})


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'status':   'ok',
        'botReady': bot.is_ready(),
        'guild':    str(GUILD_ID)
    })


def run_flask():
    """Запускаем Flask в отдельном потоке."""
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


# ─── События Discord ───
@bot.event
async def on_ready():
    print(f'✅ Бот запущен как {bot.user} (ID: {bot.user.id})')
    guild = bot.get_guild(GUILD_ID)
    if guild:
        # Подгружаем всех участников в кэш (важно для presence)
        async for member in guild.fetch_members(limit=None):
            pass
        print(f'📡 Сервер: {guild.name} — {guild.member_count} участников')
    else:
        print(f'⚠️ Бот не в сервере {GUILD_ID}. Пригласи его!')


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """
    Можно логировать смены статуса — необязательно.
    Кэш discord.py обновляется автоматически, поэтому этот хендлер
    нужен только если хочешь что-то делать в реальном времени.
    """
    if before.status != after.status:
        print(f'🔄 {after.display_name}: {before.status} → {after.status}')


# ─── Запуск ───
if __name__ == '__main__':
    # Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f'🌐 Flask API запущен на порту {PORT}')

    # Discord-бот в основном потоке
    bot.run(DISCORD_TOKEN)