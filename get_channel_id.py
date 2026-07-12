import asyncio
import os
from dotenv import dotenv_values, load_dotenv
from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat

env_path = os.path.join(os.path.dirname(__file__), 'telegram_copy_bot', '.env')
load_dotenv(dotenv_path=env_path)
env_values = dotenv_values(env_path)

# ===== SIZNING MA'LUMOTLARINGIZ =====
API_ID = int(os.getenv('API_ID', env_values.get('API_ID', '36743273')))
API_HASH = os.getenv('API_HASH', env_values.get('API_HASH', 'f6a0f9a4f1bbfdd95b17061b65d1d97c'))
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', env_values.get('TELEGRAM_TOKEN', '')).strip()
PHONE_NUMBER = os.getenv('PHONE_NUMBER', env_values.get('PHONE_NUMBER', '')).strip()
# ====================================

client = TelegramClient('id_finder_session', API_ID, API_HASH)

async def ensure_client_started():
    print(f"🔐 Auth method: bot token {'yes' if TELEGRAM_TOKEN else 'no'} | phone {'yes' if PHONE_NUMBER else 'no'}")
    if TELEGRAM_TOKEN:
        await client.start(bot_token=TELEGRAM_TOKEN)
    elif PHONE_NUMBER:
        await client.start(phone=PHONE_NUMBER)
    else:
        await client.start()

async def get_channel_info(username):
    """Kanal haqida to'liq ma'lumot olish"""
    try:
        # Kanalni topish
        entity = await client.get_entity(username)
        
        # Kanal tipini aniqlash
        if hasattr(entity, 'megagroup') and entity.megagroup:
            channel_type = "Megaguruh (Kanal)"
        elif hasattr(entity, 'broadcast') and entity.broadcast:
            channel_type = "Kanal (Broadcast)"
        elif hasattr(entity, 'gigagroup') and entity.gigagroup:
            channel_type = "Gigaguruh"
        else:
            channel_type = "Guruh"
        
        return {
            'username': username,
            'id': entity.id,
            'title': entity.title if hasattr(entity, 'title') else 'Noma\'lum',
            'type': channel_type,
            'participants': getattr(entity, 'participants_count', 0),
            'is_private': getattr(entity, 'username', None) is None
        }
    except errors.UsernameNotOccupiedError:
        return {'error': f"❌ {username} topilmadi"}
    except errors.FloodWaitError as e:
        return {'error': f"⏳ Flood wait: {e.seconds} soniya"}
    except Exception as e:
        return {'error': f"❌ Xatolik: {str(e)}"}

async def main():
    print("🔍 Kanal ID larini aniqlash...\n")
    print("=" * 60)
    
    await ensure_client_started()
    me = await client.get_me()
    print(f"✅ Bot {me.first_name} (@{me.username}) sifatida ishga tushdi\n")
    
    # ===== KUZATILADIGAN KANALLAR =====
    channels = [
        'yuk_markazi_bogdod',           # 1-kanal
        'Gmail058omad',                 # 2-kanal
        'yuk_markazi_gruppaaaa',        # 3-kanal
        'Yuk_markazi_kia_hunday_isuzu_yuk', # 4-kanal
        'kia_hunday_yuk',               # 5-kanal
        'yukspriter'                    # Sizning kanalingiz (maqsad)
    ]
    
    # ===== KANALLARNI TEKSHIRISH =====
    results = {}
    for channel in channels:
        print(f"📡 Tekshirilmoqda: @{channel}...")
        info = await get_channel_info(f'@{channel}')
        results[channel] = info
        
        if 'error' in info:
            print(f"   {info['error']}")
        else:
            print(f"   ✅ ID: {info['id']}")
            print(f"   📌 Nomi: {info['title']}")
            print(f"   📊 Turi: {info['type']}")
            print(f"   👥 A'zolar: {info['participants']}")
        print("-" * 40)
        await asyncio.sleep(0.5)  # Flood oldini olish
    
    # ===== NATIJALARNI JADVAL KO'RINISHIDA =====
    print("\n" + "=" * 60)
    print("📊 NATIJALAR JADVALI:")
    print("=" * 60)
    print(f"{'№':<3} {'Kanal':<35} {'ID':<15} {'Turi'}")
    print("-" * 60)
    
    idx = 1
    for username, info in results.items():
        if 'error' not in info:
            emoji = "🎯" if username == 'yukspriter' else "📡"
            print(f"{idx:<3} {emoji} @{username:<30} {info['id']:<15} {info['type']}")
            idx += 1
    
    # ===== KONFIGURATSIYA UCHUN TAYYOR KOD =====
    print("\n" + "=" * 60)
    print("📋 KODGA QO'YISH UCHUN TAYYOR KONFIGURATSIYA:")
    print("=" * 60)
    
    source_ids = []
    target_id = None
    
    for username, info in results.items():
        if 'error' not in info:
            if username == 'yukspriter':
                target_id = info['id']
            else:
                source_ids.append(info['id'])
    
    print("\n# MANBA KANALLAR ID LARI (5 ta):")
    print("SOURCE_CHANNELS = [")
    for idx, sid in enumerate(source_ids, 1):
        comma = "," if idx < len(source_ids) else ""
        print(f"    {sid}{comma}  # Kanal-{idx}")
    print("]")
    
    print(f"\n# MAQSAD KANAL ID (sizniki):")
    print(f"TARGET_CHANNEL = {target_id}  # @yukspriter")
    
    # ===== .ENV UCHUN =====
    print("\n" + "=" * 60)
    print("🔧 .ENV FAYL UCHUN:")
    print("=" * 60)
    print(f"\nAPI_ID={API_ID}")
    print(f"API_HASH={API_HASH}")
    print(f"TARGET_CHANNEL={target_id}")
    source_str = ",".join(str(sid) for sid in source_ids)
    print(f"SOURCE_CHANNELS={source_str}")
    
    await client.disconnect()
    print("\n✅ Ish tugadi!")

if __name__ == '__main__':
    asyncio.run(main())