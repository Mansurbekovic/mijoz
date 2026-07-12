

import asyncio
import os
import sys
import json
import logging
import time
import hashlib
import signal
import traceback
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
import re
from pathlib import Path
from collections import deque
import random

# ========== WEB SERVER (Render bepul rejasi uchun) ==========
from aiohttp import web

# ========== TELEGRAM KUTUBXONALARI ==========
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument
from telethon.errors import (
    FloodWaitError, ChannelPrivateError, ChannelInvalidError,
    UserNotMutualContactError, RPCError
)

# ========== QO'SHIMCHA KUTUBXONALAR ==========
from dotenv import load_dotenv
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, RetryError, before_sleep_log
)
from cachetools import TTLCache, LRUCache
import aiofiles
from contextlib import asynccontextmanager

# ========== UVLOOP SOZLASH (Windows uchun ixtiyoriy) ==========
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    logger = logging.getLogger(__name__)
    logger.warning("⚠️ uvloop mavjud emas, stdlib asyncio ishlatiladi")

# ========== SIGNAL HANDLER ==========
def signal_handler(signum, frame):
    """Signal handler for graceful shutdown"""
    logger.info(f"⚠️ Signal {signum} qabul qilindi, bot to'xtatilmoqda...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ========== ASOSIY PAPKA ==========
BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

# ========== LOG SOZLASH ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(BASE_DIR / 'bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ========== .ENV YUKLASH ==========
load_dotenv(dotenv_path=BASE_DIR / '.env')


def normalize_channel_value(value: Optional[str]) -> str:
    """Telegram link yoki @username ni oddiy username ga aylantiradi."""
    if value is None:
        return ""

    value = str(value).strip()
    if not value:
        return ""

    value = value.replace("https://t.me/", "")
    value = value.replace("http://t.me/", "")
    value = value.replace("https://telegram.me/", "")
    value = value.replace("http://telegram.me/", "")
    value = value.replace("t.me/", "")

    if value.startswith("@"):
        value = value[1:]
    if value.startswith("/"):
        value = value.lstrip("/")

    return value


def parse_channel_list(raw_value: Optional[str]) -> List[str]:
    """Comma-separated env qiymatini ro'yxatga aylantiradi."""
    if not raw_value:
        return []

    channels = []
    for item in raw_value.split(','):
        normalized = normalize_channel_value(item)
        if normalized:
            channels.append(normalized)
    return channels


# ========== KONFIGURATSIYA ==========
class BotState(Enum):
    """Bot holatlari"""
    STARTING = "starting"
    WORKING = "working"
    RESTING = "resting"
    STOPPED = "stopped"
    ERROR = "error"
    RECOVERING = "recovering"

@dataclass
class WorkSchedule:
    """Ish grafigi"""
    WORK_DURATION: int = 5 * 60 * 60  # 5 soat (sekundda)
    REST_DURATION: int = 30 * 60      # 30 daqiqa (sekundda)
    
    # Xavfsizlik cheklovlari
    MAX_MESSAGES_PER_MINUTE: int = 25
    MAX_MESSAGES_PER_WORK_CYCLE: int = 2000
    MAX_RETRY_ATTEMPTS: int = 10
    RETRY_DELAY: int = 5
    
    # Monitoring
    HEALTH_CHECK_INTERVAL: int = 60   # Har 60 sekundda sog'lik tekshiruvi
    
    # Qayta ishga tushirish
    AUTO_RESTART: bool = True
    RESTART_DELAY: int = 10

@dataclass
class BotConfig:
    """Bot konfiguratsiyasi"""
    # API
    API_ID: int = int(os.getenv('API_ID', '0'))
    API_HASH: str = os.getenv('API_HASH', '')
    TELEGRAM_SESSION_STRING: str = os.getenv('TELEGRAM_SESSION_STRING') or os.getenv('SESSION_STRING') or os.getenv('SESSION') or ''
    PHONE_NUMBER: str = os.getenv('PHONE_NUMBER', '')
    
    # Kanallar
    TARGET_CHANNEL: str = normalize_channel_value(os.getenv('TARGET_CHANNEL', 'yukspriter'))
    SOURCE_CHANNELS: List[str] = field(
        default_factory=lambda: parse_channel_list(os.getenv('SOURCE_CHANNELS', ''))
    )
    
    # Kalit so'zlar
    KEYWORDS: List[str] = field(
        default_factory=lambda: [
            'yuk', 'isuzu', 'kia', 'hundau', 'sprintr', 'sprintir',
            'oltiariq', 'uzum', 'shaftoli', 'qoshimcha',
            'changan', 'labo', 'damas',
            'юк', 'исузу', 'киа', 'хундау', 'спринтр', 'спринтир',
            'олтиарик', 'узум', 'шафтоли', 'кошимча',
            'чанган', 'лабо', 'дамас'
        ]
    )
    
    # Ish grafigi
    schedule: WorkSchedule = field(default_factory=WorkSchedule)
    
    # Kesh
    CACHE_SIZE: int = 1000
    CACHE_TTL: int = 600

# ========== XABAR KESHI ==========
class MessageCache:
    """Xabarlar keshi - qayta ko'chirishni oldini olish"""
    
    def __init__(self, max_size: int = 10000):
        self.cache = LRUCache(maxsize=max_size)
        self.message_ids = deque(maxlen=max_size)
        self._lock = asyncio.Lock()
        self.cache_file = BASE_DIR / "message_cache.json"
        self._load_cache()

    def _load_cache(self):
        """Oldingi ko'chirilgan xabarlar tarixini fayldan yuklash"""
        if not self.cache_file.exists():
            return

        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, timestamp in data.items():
                    try:
                        self.cache[key] = datetime.fromisoformat(timestamp)
                    except Exception:
                        self.cache[key] = datetime.now()
                    self.message_ids.append(key)
        except Exception as exc:
            logger.warning(f"⚠️ Kesh faylini o'qishda xatolik: {exc}")

    def _save_cache(self):
        """Ko'chirilgan xabarlar tarixini faylga saqlash"""
        try:
            payload = {
                key: timestamp.isoformat()
                for key, timestamp in self.cache.items()
            }
            self.cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"⚠️ Kesh faylga yozishda xatolik: {exc}")
    
    async def add(self, message_id: int, channel_id: int) -> bool:
        """Xabarni keshga qo'shish"""
        async with self._lock:
            key = f"{channel_id}:{message_id}"
            if key in self.cache:
                return False
            self.cache[key] = datetime.now()
            self.message_ids.append(key)
            self._save_cache()
            return True
    
    async def exists(self, message_id: int, channel_id: int) -> bool:
        """Xabar keshda bormi tekshirish"""
        async with self._lock:
            key = f"{channel_id}:{message_id}"
            return key in self.cache
    
    async def clear_old(self, max_age_seconds: int = 3600):
        """Eski xabarlarni tozalash"""
        async with self._lock:
            now = datetime.now()
            to_remove = []
            for key, timestamp in self.cache.items():
                if (now - timestamp).seconds > max_age_seconds:
                    to_remove.append(key)
            for key in to_remove:
                del self.cache[key]
            self._save_cache()
            logger.debug(f"🧹 Kesh tozalandi: {len(to_remove)} ta xabar")

# ========== BOT MONITOR ==========
class BotMonitor:
    """Bot sog'lig'ini kuzatish"""
    
    def __init__(self, schedule: WorkSchedule):
        self.schedule = schedule
        self.state = BotState.STARTING
        self.start_time = None
        self.work_start_time = None
        self.rest_start_time = None
        self.message_count = 0
        self.error_count = 0
        self.total_work_cycles = 0
        self._lock = asyncio.Lock()
        self.health_history = deque(maxlen=100)
    
    async def update_state(self, state: BotState):
        """Holatni yangilash"""
        async with self._lock:
            old_state = self.state
            self.state = state
            
            if state == BotState.WORKING:
                self.work_start_time = datetime.now()
                self.message_count = 0
                logger.info(f"🔄 Holat: {old_state.value} → {state.value}")
            elif state == BotState.RESTING:
                self.rest_start_time = datetime.now()
                logger.info(f"🔄 Holat: {old_state.value} → {state.value}")
            elif state == BotState.STARTING:
                self.start_time = datetime.now()
                logger.info(f"🔄 Holat: {old_state.value} → {state.value}")
    
    async def add_message(self):
        """Xabar qo'shish"""
        async with self._lock:
            self.message_count += 1
    
    async def add_error(self):
        """Xatolik qo'shish"""
        async with self._lock:
            self.error_count += 1
    
    async def get_status(self) -> Dict:
        """Holat ma'lumotlarini olish"""
        async with self._lock:
            now = datetime.now()
            
            # Ishlash vaqti
            work_duration = 0
            rest_duration = 0
            
            if self.work_start_time:
                work_duration = (now - self.work_start_time).seconds
            
            if self.rest_start_time:
                rest_duration = (now - self.rest_start_time).seconds
            
            return {
                'state': self.state.value,
                'uptime': (now - self.start_time).seconds if self.start_time else 0,
                'work_duration': work_duration,
                'rest_duration': rest_duration,
                'messages_copied': self.message_count,
                'errors': self.error_count,
                'total_cycles': self.total_work_cycles,
                'health': self.get_health_score()
            }
    
    def get_health_score(self) -> str:
        """Sog'lik skori"""
        if self.error_count > 10:
            return "⚠️ PAST"
        elif self.error_count > 5:
            return "⚡ O'RTA"
        else:
            return "✅ YUQORI"
    
    async def reset_error_count(self):
        """Xatolik sonini reset qilish"""
        async with self._lock:
            self.error_count = 0

# ========== ASOSIY BOT ==========
class AdvancedCopyBot:
    """Mukammal bot"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.schedule = config.schedule
        self.client = None
        self.cache = MessageCache()
        self.monitor = BotMonitor(self.schedule)
        self.session_name = str(BASE_DIR / f"session_{config.API_ID}")
        self.session_string = config.TELEGRAM_SESSION_STRING
        self._running = False
        self._work_task = None
        self._health_task = None
        self._lock = asyncio.Lock()
        
        # Statistika
        self.daily_stats = {
            'total_copied': 0,
            'channels_activity': {},
            'keywords_used': {},
            'last_activity': None
        }
    
    # ========== BOTNI BOSHQARISH ==========
    async def initialize(self):
        """Botni initializatsiya qilish"""
        try:
            logger.info("🚀 Bot initializatsiya qilinmoqda...")
            
            # Client yaratish
            session_provider = StringSession(self.session_string) if self.session_string else self.session_name
            self.client = TelegramClient(
                session_provider,
                self.config.API_ID,
                self.config.API_HASH,
                connection_retries=5,
                retry_delay=3,
                timeout=60,
                auto_reconnect=True,
                flood_sleep_threshold=120
            )
            
            # Event handlerlar
            @self.client.on(events.NewMessage(chats=self.config.SOURCE_CHANNELS))
            async def message_handler(event):
                await self.handle_message(event)
            
            logger.info("✅ Bot muvaffaqiyatli initializatsiya qilindi")
            return True
            
        except Exception as e:
            logger.error(f"❌ Initialize xatolik: {e}")
            logger.error(traceback.format_exc())
            return False
    
    async def start(self):
        """Botni ishga tushirish"""
        try:
            logger.info("🌟 Bot ishga tushmoqda...")
            
            # Clientga ulanish
            session_string = os.getenv('TELEGRAM_SESSION_STRING') or os.getenv('SESSION_STRING') or os.getenv('SESSION')
            if session_string:
                logger.info("🪪 Session string topildi, mavjud sessiya bilan ulanmoqda")
                await self.client.start(phone=None)
            else:
                phone = os.getenv('PHONE_NUMBER') or os.getenv('TELEGRAM_PHONE')
                if phone:
                    logger.info("📱 Phone number topildi, user-account login ishlatilmoqda")
                    await self.client.start(phone=phone)
                else:
                    token = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN') or os.getenv('TG_BOT_TOKEN')
                    if token:
                        logger.info("🪪 Telegram bot token topildi, token orqali autentifikatsiya qilinmoqda")
                        await self.client.start(bot_token=token)
                    else:
                        logger.error("❌ Telegram credential topilmadi")
                        raise RuntimeError("No Telegram credentials configured")

            me = await self.client.get_me()
            
            logger.info(f"✅ Bot {me.first_name} (@{me.username}) sifatida ishga tushdi")
            
            # Kanallarni tekshirish
            valid = await self.validate_channels()
            logger.info(f"📡 {len(valid)}/{len(self.config.SOURCE_CHANNELS)} ta kanal tekshirildi")
            
            # Monitoringni boshlash
            await self.monitor.update_state(BotState.WORKING)
            
            # Ishlash loop'ini boshlash
            self._running = True
            await self.work_cycle()
            
        except Exception as e:
            logger.error(f"❌ Ishga tushirish xatolik: {e}")
            logger.error(traceback.format_exc())
            raise
    
    async def work_cycle(self):
        """Asosiy ish sikli - 5 soat ish, 30 daqiqa dam"""
        cycle_count = 0
        
        while self._running:
            try:
                cycle_count += 1
                logger.info(f"🔄 Ish sikli #{cycle_count} boshlandi")
                logger.info(f"⏰ 5 soat ish rejimi...")
                
                # 5 soat ishlash
                await self.monitor.update_state(BotState.WORKING)
                await self.work_phase()
                
                if not self._running:
                    break
                
                # 30 daqiqa dam olish
                logger.info(f"☕ 30 daqiqa dam olish vaqti...")
                await self.monitor.update_state(BotState.RESTING)
                await self.rest_phase()
                
            except Exception as e:
                logger.error(f"❌ Ish sikli xatolik: {e}")
                logger.error(traceback.format_exc())
                
                # Xatolikdan keyin tiklanish
                await self.recover_from_error()
    
    async def work_phase(self):
        """5 soatlik ish fazasi"""
        work_end = datetime.now() + timedelta(seconds=self.schedule.WORK_DURATION)
        
        # Sog'lik tekshiruvi
        self._health_task = asyncio.create_task(self.health_check())
        
        while self._running and datetime.now() < work_end:
            try:
                # Cheklovlarni tekshirish
                if await self.check_limits():
                    await asyncio.sleep(0.1)
                    continue

                await self.poll_recent_messages()
                
                # Bir oz kutish (CPU yukini kamaytirish)
                await asyncio.sleep(0.5)
                
                # Xatoliklar juda ko'p bo'lsa
                status = await self.monitor.get_status()
                if status['errors'] > 15:
                    logger.warning("⚠️ Xatoliklar juda ko'p, tiklanish kutilmoqda...")
                    await self.recover_from_error()
                
            except asyncio.CancelledError:
                logger.info("⏹️ Ish fazasi to'xtatildi")
                break
            except Exception as e:
                logger.error(f"❌ Ish fazasi xatolik: {e}")
                await self.monitor.add_error()
                await asyncio.sleep(1)
        
        # Sog'lik tekshiruvini to'xtatish
        if self._health_task:
            self._health_task.cancel()
        
        logger.info("✅ 5 soatlik ish fazasi tugadi")
    
    async def rest_phase(self):
        """30 daqiqalik dam olish fazasi"""
        rest_end = datetime.now() + timedelta(seconds=self.schedule.REST_DURATION)
        
        # Xatoliklarni reset qilish
        await self.monitor.reset_error_count()
        
        # Keshlarni tozalash
        await self.cache.clear_old()
        
        while self._running and datetime.now() < rest_end:
            try:
                # Dam olish vaqtida xabarlarni qabul qilmaydi
                await asyncio.sleep(1)
                
                # Har 5 daqiqada holatni ko'rsatish
                if int((datetime.now() - rest_end + timedelta(seconds=self.schedule.REST_DURATION)).seconds / 60) % 5 == 0:
                    remaining = int((rest_end - datetime.now()).seconds / 60)
                    logger.info(f"☕ Dam olish: {remaining} daqiqa qoldi")
                
            except asyncio.CancelledError:
                logger.info("⏹️ Dam olish fazasi to'xtatildi")
                break
            except Exception as e:
                logger.error(f"❌ Dam olish xatolik: {e}")
                await asyncio.sleep(1)
        
        logger.info("✅ 30 daqiqalik dam olish tugadi")
    
    async def health_check(self):
        """Sog'lik tekshiruvi - har 60 soniyada"""
        try:
            while self._running:
                await asyncio.sleep(self.schedule.HEALTH_CHECK_INTERVAL)
                
                status = await self.monitor.get_status()
                logger.info(f"💚 Sog'lik tekshiruvi: {status}")
                
                # Agar ko'p xatolik bo'lsa
                if status['errors'] > 10:
                    logger.warning("⚠️ Xatoliklar ko'p, tiklanish kerak")
                    await self.recover_from_error()
                
                # Client ulanishini tekshirish
                if self.client and not self.client.is_connected():
                    logger.warning("⚠️ Client uzilgan, qayta ulanmoqda...")
                    await self.client.reconnect()
                
        except asyncio.CancelledError:
            logger.info("⏹️ Sog'lik tekshiruvi to'xtatildi")
        except Exception as e:
            logger.error(f"❌ Sog'lik tekshiruvi xatolik: {e}")
    
    async def recover_from_error(self):
        """Xatolikdan tiklanish"""
        logger.info("🔄 Xatolikdan tiklanish boshlanmoqda...")
        await self.monitor.update_state(BotState.RECOVERING)
        
        try:
            # Clientni qayta ulash
            if self.client:
                await self.client.disconnect()
                await asyncio.sleep(2)
                await self.client.connect()
            
            # Keshlarni tozalash
            await self.cache.clear_old()
            
            # Xatoliklarni reset qilish
            await self.monitor.reset_error_count()
            
            # Kutish
            await asyncio.sleep(5)
            
            logger.info("✅ Tiklanish muvaffaqiyatli")
            await self.monitor.update_state(BotState.WORKING)
            
        except Exception as e:
            logger.error(f"❌ Tiklanish xatolik: {e}")
            await asyncio.sleep(10)
    
    async def poll_recent_messages(self):
        """Manba kanallardagi so'nggi xabarlarni tekshirib, mos kelganlarini ko'chirish."""
        if not self.client or self.monitor.state != BotState.WORKING:
            return

        for channel in self.config.SOURCE_CHANNELS:
            try:
                entity = await self.client.get_entity(channel)
            except Exception as exc:
                logger.debug(f"⚠️ Kanalni olishda xatolik: {channel} - {exc}")
                continue

            try:
                messages = await self.client.get_messages(
                    entity,
                    limit=5,
                    offset_date=datetime.now() - timedelta(seconds=60)
                )
            except Exception as exc:
                logger.debug(f"⚠️ Xabarlarni olishda xatolik: {channel} - {exc}")
                continue

            for message in reversed(messages):
                if getattr(message, 'out', False):
                    continue

                text = message.text or message.caption or ''
                if not text:
                    continue

                text_lower = text.lower()
                normalized_keywords = [keyword.lower() for keyword in self.config.KEYWORDS if keyword]
                matched_keywords = [keyword for keyword in normalized_keywords if keyword in text_lower]
                if not matched_keywords:
                    continue

                channel_id = entity.id
                if await self.cache.exists(message.id, channel_id):
                    continue

                logger.info(f"📥 Poll orqali mos kelgan xabar topildi: {channel} | {text[:120]}")
                sent = await self.client.send_message(self.config.TARGET_CHANNEL, text)
                await self.cache.add(message.id, channel_id)
                await self.monitor.add_message()
                self.daily_stats['total_copied'] += 1
                self.daily_stats['last_activity'] = datetime.now()
                logger.info(f"✅ Poll orqali xabar ko'chirildi: {sent.id} | kanal: {self.config.TARGET_CHANNEL}")

    # ========== XABAR QAYTA ISHLASH ==========
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((FloodWaitError, ConnectionError))
    )
    async def handle_message(self, event):
        """Yangi xabarni qayta ishlash"""
        try:
            # Agar dam olish holatida bo'lsa, xabarlarni qabul qilmaydi
            if self.monitor.state == BotState.RESTING:
                return
            
            # Xabar matni
            text = event.message.text or event.message.caption or ''
            if not text:
                logger.info("📭 Bo'sh xabar qabul qilindi, o'tkazib yuborildi")
                return
            
            # Kalit so'z borligini tekshirish (katta/kichik harfga bog'liq emas)
            text_lower = text.lower()
            normalized_keywords = [keyword.lower() for keyword in self.config.KEYWORDS if keyword]
            matched_keywords = [keyword for keyword in normalized_keywords if keyword in text_lower]
            if not matched_keywords:
                logger.debug(f"🚫 Mos kelmadi: {text[:80]}")
                return
            
            # Kanal ma'lumotlari
            chat = await event.get_chat()
            channel_id = chat.id
            channel_name = chat.title if hasattr(chat, 'title') else str(chat.id)
            
            # Xabar allaqachon ko'chirilganmi?
            if await self.cache.exists(event.message.id, channel_id):
                logger.info(f"⏭️ Xabar allaqachon ko'chirilgan: {channel_name} -> {event.message.id}")
                return
            
            logger.info(f"📥 Mos kelgan xabar topildi: {channel_name} | kelgan so'z: {matched_keywords[0]} | matn: {text[:120]}")
            
            # Xabarni ko'chirish
            sent = await self.client.send_message(self.config.TARGET_CHANNEL, text)
            logger.info(f"✅ Xabar ko'chirildi: {sent.id} | kanal: {self.config.TARGET_CHANNEL}")
            
            # Keshga qo'shish
            await self.cache.add(event.message.id, channel_id)
            
            # Statistikani yangilash
            await self.monitor.add_message()
            self.daily_stats['total_copied'] += 1
            self.daily_stats['last_activity'] = datetime.now()
            
            # Kanal statistikasi
            if channel_name not in self.daily_stats['channels_activity']:
                self.daily_stats['channels_activity'][channel_name] = 0
            self.daily_stats['channels_activity'][channel_name] += 1
            
            logger.info(f"✅ Xabar ko'chirildi: {text[:50]}...")
            
        except FloodWaitError as e:
            logger.warning(f"⏳ Flood wait: {e.seconds} soniya")
            await asyncio.sleep(min(e.seconds, 60))
            raise
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await self.monitor.add_error()
    
    async def check_limits(self) -> bool:
        """Cheklovlarni tekshirish"""
        status = await self.monitor.get_status()
        
        # Xatolik cheklovi
        if status['errors'] > 10:
            return True
        
        # Xabarlar soni cheklovi
        if status['messages_copied'] > self.schedule.MAX_MESSAGES_PER_WORK_CYCLE:
            logger.warning("⚠️ Maksimal xabar chekloviga yetildi")
            return True
        
        return False
    
    async def validate_channels(self) -> List[str]:
        """Kanallarni tekshirish"""
        valid = []
        for channel in self.config.SOURCE_CHANNELS:
            try:
                entity = await self.client.get_entity(channel)
                valid.append(channel)
                logger.info(f"✅ Kanal tekshirildi: {channel}")
            except Exception as e:
                logger.error(f"❌ Kanal topilmadi: {channel} - {e}")
        return valid
    
    # ========== TO'XTATISH ==========
    async def stop(self):
        """Botni to'xtatish"""
        logger.info("🛑 Bot to'xtatilmoqda...")
        self._running = False
        
        if self._health_task:
            self._health_task.cancel()
        
        if self.client:
            await self.client.disconnect()
        
        await self.monitor.update_state(BotState.STOPPED)
        logger.info("✅ Bot to'xtatildi")

# ========== HEALTH-CHECK WEB SERVER ==========
async def run_health_server():
    """Render bepul rejasi Web Service turini talab qiladi va $PORT ni
    tinglashni kutadi. Shu sababli yengil HTTP server ochamiz, u faqat
    'OK' javob beradi. Haqiqiy ish esa Telegram bot tomonida davom etadi."""
    port = int(os.getenv("PORT", "10000"))

    async def health(request):
        return web.Response(text="OK - telegram-copy-bot ishlamoqda")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"🌐 Health-check server {port}-portda ishga tushdi")

    # Doim ishlab tursin
    while True:
        await asyncio.sleep(3600)


# ========== ASOSIY FUNKSIYA ==========
async def run_bot():
    """Telegram botni ishga tushirish (avtomatik qayta urinish bilan)"""
    while True:
        try:
            # Konfiguratsiya
            config = BotConfig()

            # API tekshiruvi
            if not config.API_ID or not config.API_HASH:
                logger.error("❌ API_ID yoki API_HASH topilmadi!")
                logger.info("Iltimos, Render'dagi Environment Variables bo'limini tekshiring")
                await asyncio.sleep(30)
                continue

            # Bot yaratish
            bot = AdvancedCopyBot(config)

            # Initializatsiya
            if not await bot.initialize():
                logger.error("❌ Bot initializatsiya qilinmadi")
                await asyncio.sleep(30)
                continue

            # Botni ishga tushirish
            await bot.start()

        except KeyboardInterrupt:
            logger.info("🛑 Bot to'xtatildi (Ctrl+C)")
            break
        except Exception as e:
            logger.error(f"❌ Kutilmagan xatolik: {e}")
            logger.error(traceback.format_exc())
            logger.info("🔄 30 soniyadan keyin qayta ishga tushiriladi...")
            await asyncio.sleep(30)


async def main():
    """Health-check serverni va Telegram botni parallel ishga tushiradi"""
    await asyncio.gather(
        run_health_server(),
        run_bot(),
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot to'xtatildi")
    except Exception as e:
        logger.error(f"❌ Kutilmagan xatolik: {e}")
        sys.exit(1)